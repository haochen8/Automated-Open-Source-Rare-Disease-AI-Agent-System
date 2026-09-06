# Initial architecture decisions

## Scope

Phases 0–4 provide the repository foundation, a normalized VCF → Parquet → DuckDB flow, a bounded
agent-controlled filtering workflow, and deterministic phenotype/inheritance ranking proven with
synthetic data. Track 2 remains deferred.

## Separation of concerns

1. Restricted patient data lives outside Git and is referenced by configuration.
2. VCF parsing and future annotation are deterministic and streaming.
3. Parquet is the canonical columnar interchange; DuckDB performs bounded, parameterized queries.
4. Future agents receive compact tool results, never millions of raw variants.
5. LLM inference is a replaceable protocol. Scientific processing does not import a model SDK.
6. Public-source caches and restricted run artifacts have distinct configured paths.

The normalized record includes chromosome, position, alleles, identifier, gene/transcript,
consequence, protein change, proband and multi-sample genotype calls, genotype quality, variant
quality, allele frequency, ClinVar classification, three optional pathogenicity scores,
inheritance notes, and phenotype/evidence score fields.

## Hardware boundary

Local development assumes Apple Silicon, Metal, 16 GB unified memory, and no CUDA. The command
`rare-disease-agent models recommend` reports total/available memory, disk, architecture, Metal,
and a conservative parameter ceiling. It filters a small curated catalog and never installs or
launches anything.

For 12–23.9 GB hosts the normal ceiling is 8B. At 24 GB and above it can recommend up to 14B,
which is also the absolute development-policy cap. Runtime estimates include weight and context
overhead and are deliberately conservative. Current available memory affects the launch-readiness
warning but not the host's overall capacity classification.

Development roles share one local backend instance. Later, the same role configuration can point
to distinct compliant self-hosted OpenAI-compatible, vLLM, MLX, Ollama, or llama.cpp backends. No
scientific pipeline rewrite should be required.

## Phase 2 control flow

LangGraph owns explicit `initial_inspection → variant_agent → execute_tool → finalize` transitions.
The Pydantic state is revalidated at each node boundary because graph outputs are mappings. The
agent returns one schema-validated decision at a time; a dispatcher maps it to one typed tool.

Candidate filtering is reversible. Every filter writes a new branch, and the source remains intact.
The mock reference plan creates conservative, pathogenicity, and phenotype-ready branches before
unioning their refined descendants. A candidate-floor guard deletes and rejects an over-aggressive
new branch without affecting its parent.

Termination is enforced by iteration and tool-call budgets, invalid-response and tool-error limits,
canonical repeated-decision detection, no-reduction limits, and an explicit stop action. The target
candidate count is context for the planner, never an automatic mandate to over-filter.

## Phase 3 evidence and ranking

The same agent loop adds four typed actions: validate the patient HPO set, rank candidate genes by
phenotype, evaluate inheritance hypotheses, and create evidence branches. Only compact top-gene and
inheritance-count summaries enter LangGraph state. Full genotype calls, variant rows, scores, and
branch membership remain outside the prompt.

The phenotype layer loads an OBO ontology and a replaceable gene-association TSV. It validates HPO
syntax, reports unknown or invalid terms, resolves supported obsolete replacements, and calculates a
reproducible Resnik best-match-average score. A miniature checksum-pinned synthetic release supports
offline CI. The explicit public importer accepts only unauthenticated HTTPS artifacts from approved
HPO hosts, limits response size, optionally verifies a pinned checksum, and records source, release,
timestamp, checksum, and license reference.

Inheritance evaluators operate on extensible pedigrees and typed genotype calls. They emit evidence,
fit, quality status, and warnings for autosomal dominant, homozygous/autosomal recessive, de novo,
X-linked, and compound-heterozygous models. Missing parental calls keep de novo evidence uncertain.
Compound-heterozygous phase is `confirmed_trans` only when opposite parental origins are observed;
otherwise it remains `possible_trans` or `unknown`.

The preliminary ranker computes separately inspectable quality, rarity, consequence, phenotype, and
inheritance features. Configurable defaults are a weighted linear baseline, not a scientific
optimality claim. Four automatic ablations compare filtering only, phenotype, inheritance, and both.
An additional ablation removes one HPO term.

## Persistent branch design

Candidate membership is now persisted in a run-local DuckDB database with `run_id`, `variant_id`,
`branch`, `stage`, `active`, and `reason_code`. Filters create new rows by parameterized SQL templates;
source branches remain unchanged. Phase 3 constructs conservative, phenotype-priority,
inheritance-priority, pathogenicity-priority, and novel-gene-rescue branches before unioning them.
The rescue branch ensures absence of a known HPO association is never treated as exclusion evidence.
Per-ablation ranking features and ranks are also persisted in DuckDB.

## Phase 4 persistent runtime and recovery

Phenotype and inheritance operations now write typed evidence in batches into indexed DuckDB,
keyed by run, variant/gene, method and data version. Mixed versions are rejected. Each evidence
operation is transactional; interrupted generators roll back all batches. Resnik information
content counts genes without keeping complete gene-to-score dictionaries. SQL joins drive evidence
branches and rank features; deterministic ordering uses score then variant identifier. Complete
artifacts are streamed, with only bounded summaries retained in graph state and return values.

The critic has only typed read-only re-inspection requests, capped at two. It checks required
evidence, provenance, rescue survival, competing models and uncertainty language. It cannot change
scores, branches or planner budgets. The local report projects allow-listed fields and replaces
candidate identities with salted pseudonyms; the complete evidence stays separately inspectable.

A versioned stage journal owns restart identity and immutable stage events. A filesystem lock
serializes runs. Checksummed stage publication avoids partial results, while failed uncommitted
stages are recomputed. Self-contained persisted variant tables survive staging-directory renames.
Committed source membership and audit events are never reset during resume.

Public downloads use a separate explicit lock/receipt lifecycle. Official-shaped HPO inputs are
normalized offline into typed Parquet and indexed tables, with licenses and source/output hashes.
Authorized patient rehearsal requires exact input/output scope outside Git and fail-closed preflight;
annotation preparation remains a separately authorized established-tool operation.

## Privacy defense in depth

- `.gitignore` blocks raw/derived genomic formats, local models, caches, runs, and secrets.
- A pre-commit/CI guard checks paths, content markers, and credential patterns.
- Synthetic VCF-like fixtures use `.vcf.txt`, live only in test or bundled resource directories,
  and contain `##synthetic=true`.
- Structured logging redacts patient/genotype/variant/credential-like fields by default.
- Remote patient-data transfer defaults to false.
- External LangSmith tracing is disabled in the environment example; audits stay local.

The guard is intentionally conservative. It supplements rather than replaces operational access
controls and manual review.

## Failure and scaling characteristics

The VCF parser yields one normalized record at a time. Parquet writing batches 10,000 records, so
memory is bounded. DuckDB reads Parquet directly and branch membership never enters LangGraph state.
Multi-allelic lines are split into one normalized record per alternate allele. Multi-sample `GT` and
`GQ` values are retained as structured JSON for deterministic inheritance evaluation. The parser
still assumes pre-normalized synthetic annotations; mature cohort and annotation handling will use
established bioinformatics tools.

Each Phase 3 run records prompt identity, tool/data versions, method parameters, Git commit and dirty
status, Python and dependency versions, the HPO checksum, causal top-k metrics, and memory use.
