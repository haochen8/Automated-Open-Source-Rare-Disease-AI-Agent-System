# Phase 5 bounded end-to-end execution

This workflow completes the first narrowly scoped Track 1 integration dry run described in the
Phase 5 brief. It consumes an existing, integrity-verified annotation rehearsal. It does not
perform whole-genome annotation or claim case-wide candidate coverage. All patient-derived
artifacts and metrics, including aggregate summaries, remain outside Git.

## Local interface

Create a private JSON configuration matching `workflows.phase5.Phase5Input`, containing the existing
original VCF/index/phenotype paths, verified annotation-rehearsal directory, normalized HPO directory,
new external output directory, run ID and explicit local research-use authorization.
`confirmed_affected_sample: false` means unknown, never unaffected. No role, sex or parentage is
inferred. Phenotype scoring is conditional on the supplied document describing the analyzed sample;
the private report flags unconfirmed linkage and inheritance fits remain uncertain.

Preview with `rare-disease-agent phase5 plan /private/path/config.json`, then execute the authorized
scope with `rare-disease-agent phase5 run /private/path/config.json`. In a development checkout,
set `PYTHONPATH=src` and use that checkout's Python environment. The supervisor requires permission
to inspect local process memory. It enforces a 3 GiB process-family RSS cap, 2 GiB output cap,
one-hour runtime cap, 25 GiB free-disk reserve and 256 MiB minimum live available memory. It starts
with a controlled environment, disabled tracing and no inherited credentials.

If the host denies process inspection, the supervisor stops the worker and reports
`Host process inspection permission denied` rather than a resource-threshold failure.
This means required monitoring was unavailable, not that a measured limit was exceeded.
Retry requires an execution environment that permits process inspection; the limits remain
mandatory. The diagnostic does not expose process names, IDs, paths or raw host errors.

## Integrity and representation

When resume rejects a changed identity, the diagnostic names only fixed input or provenance
categories, such as the phenotype document fingerprint, original index path identity, HPO resource
manifest identity, source-code identity or dataset revision identity. Fingerprints still include
checksum, size and modification time; even a timestamp-only change prevents resume. Path identity
changes also prevent resume when file contents are identical. Paths, basenames, metadata values,
run ID values and checksums are never included in these diagnostics.

A mismatch requires a fresh run in a new output directory with matching verified annotation
provenance. Preserve the old journal and prepared stages; do not edit their identities to force
resume. An annotation source checksum mismatch is reported before preparation or stage reuse.
The full configuration hash remains authoritative; differences that cannot be classified use a
generic configuration/resource identity diagnostic and still fail closed. Categories explain
observed differences, not whether the old or current metadata is scientifically correct.

The supervised CLI displays the same fixed-label diagnostic. Its local failure receipt, beside
the run directory at `phase5-<invocation_id>-failure.json`, contains allowlisted `identity_mismatch`
codes alongside the existing exception-class and frame records. Each supervisor invocation passes
a fresh random UUID to its worker and accepts only a receipt with that same invocation identity.
Sibling outputs sharing a run ID and repeated invocations therefore use separate receipts.
Missing or mismatched receipts retain a generic identity refusal. Unrelated failures retain their
generic private diagnostics. No raw exception message is passed through the worker receipt or CLI.

If a committed stage cannot pass artifact validation, resume also stops without rewriting its
journal or stage artifacts. The diagnostic identifies preparation (`prepare`), ingestion (`ingest`),
analysis or deliverables using fixed labels, and reports missing artifacts, an inconsistent file
inventory, a checksum validation failure or inability to read artifacts. These categories describe
the failed check, not the cause of corruption. Unknown or invalid stage identities never appear
verbatim. No artifact names, paths, values, contents or checksums are included.

Preserve the existing run for local inspection. A fresh run in a new output directory with matching
verified annotation provenance is required; do not edit the journal, delete committed stages, or
regenerate them in place to force resume. Interrupted **uncommitted** stages still follow the normal
recomputation path. The worker receipt carries only allowlisted `stage_integrity.stage` and
`stage_integrity.reason` codes for this refusal. The supervisor requires the corresponding worker
exit status and matching invocation identity; missing, malformed or stale receipts keep a generic
committed-stage refusal with the same recovery guidance. Input/provenance identity checks remain
authoritative and run before committed-stage reuse validation.

- Original input fingerprints, annotation input/output checksums, resource manifest identity and
  source-code checksum bind each run. A changed configuration/input cannot silently resume an old run.
- Streaming reconciliation checks original alternate ordinals and projected GT/GQ values against
  normalization. Annotation must preserve normalized allele records and genotype values.
- The importer retains every allele and every CSQ entry in separate Parquet tables. Each ranking
  candidate represents one source allele and gene. A mapping table links candidates to allele
  provenance and the representative consequence used for scoring; all other consequences remain.
- The representative is the highest configured consequence score for that gene, with deterministic
  ties. This is a transparent reduction for the existing ranking schema, not transcript deletion.
- Frequencies come only from explicit gnomAD fields. Missing frequency stays unknown. Caller AF
  cannot substitute for population frequency. Unsupported contigs remain in a deferred ledger.
- The current execution context supports the autosomal rehearsal. Sex-chromosome/PAR, nonstandard
  contigs and full-input coverage require separate validated handling before a broader run.
- Every stage publishes atomically. Completed stages have verified checksums; failed staging is
  recomputed. Synthetic tests exercise interruption, resume, changed inputs and corrupted annotations.
- Pair evidence is streamed in bounded batches inside one transaction. Repeated evidence keys retain
  the strongest score and last equal-score payload; a failed pair operation rolls back its pair rows
  and evidence together. Candidate pairs must share a gene and chromosome.
- Empty priority subbranches are valid in this bounded run if the full conservative source remains
  intact. They are retained and reported as zero. The ensemble includes the conservative branch,
  preserving every imported candidate; general workflows keep their existing candidate-floor guard.

## Private outputs

The run directory contains `run.json`, `stage_audit.jsonl`, `integrity_manifest.json`, preparation,
ingestion, analysis and deliverables directories, plus separate supervision measurements.
Preparation includes normalized phenotype and annotation provenance. Ingestion includes allele,
consequence, candidate-map and ranking-input Parquet tables. Analysis contains the indexed branch
and evidence database and the bounded JSON critic report. Deliverables include:

- `private_candidate_ranking.parquet`: default ablations plus six named strategy families;
- `private_candidate_features.parquet`: phenotype/inheritance evidence and provenance;
- `private_branch_membership.parquet` and `branch_metrics.json`;
- `phenotype_summary.json`, `inheritance_summary.json`, and `sanitized_metrics.json`;
- `private_research_report.md`, with explicit scope and evidence limitations.

Each named strategy also has the existing four feature ablations. The six strategies are baseline,
phenotype-heavy, inheritance-heavy, pathogenicity-heavy, ensemble and conservative. The pathogenicity
strategy weights consequence evidence; it does not invent missing clinical classifications or
predictor annotations. The conservative strategy ranks the preserved source branch. These fixed
weights are research sensitivity settings, not a calibration claim.

A completed bounded dry run proves integration against the observed input shape. It does not
identify a causal variant, establish clinical accuracy, supply a whole-case challenge submission,
or authorize an upload. Preserve the original data for later coverage expansion. See the
[Phase 6 recommendation](phase6-recommendation.md) for the proposed next evaluation phase.

Existing runs can be inspected against explicitly supplied local labels with
`phase5 evaluate`. See [case-level failure analysis](case-evaluation.md) for truth/source binding,
separate gene/allele metrics, failed-case accounting and private output requirements. This does not
expand the annotated scope or establish official accuracy.

## Larger autosomal resource benchmark

`workflows.benchmark.select_autosomal_benchmark` selects a configurable maximum of 1–500 records
per autosome, evenly spaced by eligible record ordinal. It scans the original twice, checks its
checksum, and keeps global source-row and original alternate ordinals. It counts unselected and
unsupported records separately. These are recoverable in the original file. The method is a
stratified resource benchmark, not a random sample or an assessment of causal-variant accuracy.
It rejects reserved provenance tags and conflicting contig aliases before writing a subset.
Inputs, outputs and selection metrics must be outside Git.

Run `rare-disease-agent phase5 benchmark-select /private/path/config.json --per-autosome 100`
with an authorized `Phase5Input` configuration whose `annotation_rehearsal` names a fresh directory.
This command selects records only; normalization, annotation and ranking remain separate steps.

`workflows.coverage` compares contig record counts with exact local FASTA lengths and cache names.
It separates autosomal availability from missing reference names, missing annotation cache,
reference-length conflicts and unresolved sex/mitochondrial/nonstandard context. A missing exact
name may need alias reconciliation rather than another download. If an old index lacks count
metadata, a streaming VCF count is available; the index is never rebuilt to obtain statistics.
Matching lengths and cache directories alone do not validate sequence content or genotype ploidy.

Sparse genome-wide samples may need many more cache regions than a contiguous sample of the same
size. `ToolJob.buffer_size` controls VEP's internal batch (default 500; bounded 1–5000). Reduce it
when necessary and retain the memory guard. A single worker omits `--fork` entirely because VEP
otherwise still enters its child-process path. The optional process observation records sampled
peak RSS, output size and elapsed time, including measurements before a resource-boundary failure.
Use a fresh private run for changed execution settings; preserve completed historical artifacts.

Genotype reconciliation uses one database transaction and one database thread within its 128 MiB
limit. The larger synthetic regression covers thousands of alleles. Compound pairing requires
an assigned gene and matching chromosome; placeholder unknown genes cannot establish a shared
gene. The persistent pair query also requires active branch membership. Individual evidence and
conservative membership remain available for unassigned variants.

Verified disjoint shard assemblies are also accepted as `annotation_rehearsal`. Phase 5 verifies
the assembly and nested table checksums, copies the complete prepared tables, and recomputes
evidence and ranking globally. It retains source coverage and shard provenance in private outputs.
See [process-isolated coverage batches](phase5-scaling.md#verified-global-assembly-and-process-isolation)
for authorization, resource bounds, compatible inputs and recovery behavior.
