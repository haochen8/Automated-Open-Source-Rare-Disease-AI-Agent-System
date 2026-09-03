# Initial architecture decisions

## Scope

Phases 0–2 create the repository foundation, a normalized VCF → Parquet → DuckDB flow, and a
bounded agent-controlled filtering workflow proven with synthetic data. Track 2 remains deferred.

## Separation of concerns

1. Restricted patient data lives outside Git and is referenced by configuration.
2. VCF parsing and future annotation are deterministic and streaming.
3. Parquet is the canonical columnar interchange; DuckDB performs bounded, parameterized queries.
4. Future agents receive compact tool results, never millions of raw variants.
5. LLM inference is a replaceable protocol. Scientific processing does not import a model SDK.
6. Public-source caches and restricted run artifacts have distinct configured paths.

The normalized Phase 1 record includes chromosome, position, alleles, identifier, gene/transcript,
consequence, protein change, genotype/zygosity, quality, allele frequency, ClinVar classification,
three optional pathogenicity scores, inheritance notes, and later phenotype/evidence scores.

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

The Phase 2 branch index is deliberately in memory because this phase accepts synthetic inputs only.
The typed tool boundary allows a later DuckDB-temporary-table or persisted-Parquet implementation to
replace it for patient-scale datasets without changing agent decisions or graph transitions.

## Privacy defense in depth

- `.gitignore` blocks raw/derived genomic formats, local models, caches, runs, and secrets.
- A pre-commit/CI guard checks paths, content markers, and credential patterns.
- Synthetic VCF-like fixtures use `.vcf.txt`, live only in test or bundled resource directories,
  and contain `##synthetic=true`.
- Structured logging redacts patient/genotype/variant/credential-like fields by default.
- Remote patient-data transfer defaults to false.
- External LangSmith tracing is disabled in the environment example; Phase 2 audit stays local.

The guard is intentionally conservative. It supplements rather than replaces operational access
controls and manual review.

## Failure and scaling characteristics

The VCF parser yields one normalized record at a time. Parquet writing batches 10,000 records, so
memory is bounded. DuckDB reads Parquet directly and returns at most 10,000 rows through the typed
filter API. Multi-allelic lines are split into one normalized record per alternate allele. This
prototype assumes one sample and pre-normalized annotations; mature cohort and annotation handling
will use established bioinformatics tools.
