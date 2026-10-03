# Phase 5 scaling preparation

## Compact ranking storage

Fresh databases store ranking values with numeric candidate and ranking-context keys. The
`ranked_evidence` view retains the previous columns, candidate IDs, feature JSON and provenance
interface used by reports and exports. A context stores one provenance record per run and mode.
All four ablations in one ranking call publish in a single transaction. Re-ranking replaces each
whole mode, removing stale rows while preserving other contexts. Failed re-ranking rolls back the
scores and provenance together. Existing table-based databases remain readable and are never
migrated in place. An attached historical database does not prevent fresh local storage creation.

A generated benchmark with 5,000 synthetic candidates and 140,000 ranking rows compared the old
implementation at commit `6147dc5` with compact storage. Database size decreased from 18,886,656 to
7,614,464 bytes. Candidate IDs, ordering, scores, features and stable provenance fields matched
exactly. This isolated synthetic result is not a full-case storage or runtime forecast. Private
replays and resource measurements must remain outside Git.

## Reference-name planning

Coverage can be reviewed with the already available VEP cache's `chr_synonyms.txt`, pinned by
checksum. The planner follows publisher-supplied relationships transitively and requires a unique
local FASTA target with the expected length. Missing reference, missing annotation cache,
length mismatch and ambiguous target remain distinct. Colliding source contigs are rejected.

An explicit planning option also considers `chr` prefix conventions and `M`/`MT`, consistent with
the naming conventions handled by the installed VEP parser. These remain proposals: the planner
does not rename sequences, change coordinates, validate sequence equivalence, infer sex, or extend
inheritance-model coverage. Record-level REF checks and genomic context validation are required
before any proposed mapping is used for analysis. No additional reference download is implied.

## Compound-pair storage

Compound-pair payloads are inserted in batches of at most 128 within the same transaction as
their evidence. Per-pair SQL inserts can accumulate DuckDB transaction buffers until memory is
exhausted even when the final table is small. Batching preserves candidate enumeration, pair
payloads, replacement semantics, evidence score reduction and the 100,000-pair work budget.
A failed operation rolls back both pair rows and pair evidence, including batches already flushed.
The synthetic dense-gene regression writes and replaces all 780 pairs under a 64 MB DuckDB limit;
production memory limits are unchanged. This addresses storage overhead, not quadratic pair growth.

### Bounded candidate reads

Each ordered batch of at most 128 pair IDs fetches only the five genotype-input columns for its
distinct candidates in one parameterized query. At most 256 candidates are decoded once per batch;
the cache is discarded between batches. The original pair order and endpoint order are preserved,
including equal-score evidence replacement. SQL eligibility, evaluator behavior, complete payloads,
transaction boundaries, ranking and the global 100,000-pair budget remain unchanged. Missing
candidates fail closed and roll back pair evidence and pair rows.

Three fresh-process synthetic comparisons against `e7c12d9`, with 128 heterozygous candidates in
one gene (8,128 pairs), reduced candidate retrieval queries from 8,128 to 64. Median inheritance
evaluation time fell from 11.44 seconds (11.37–11.63) to 3.39 seconds (3.33–3.42). Runs used one
DuckDB thread, its existing 1 GB production limit and disabled spilling. Complete pair, evidence,
membership, ranking and observation hashes matched with controlled software provenance; actual
implementation hashes were recorded separately. Database size remained 6,041,600 bytes, and sampled
peak process RSS ranges were about 215–217 MiB before and 208–213 MiB after. A separate 40-candidate
comparison also matched under 64 MB. An initial 128-candidate baseline attempt exceeded 64 MB;
the smaller stress test and production resource limits were not changed. These synthetic timings
measure bounded evaluation only and do not establish whole-case runtime or coverage.

### Pair-specific evaluation

The persistent pair loop calls `evaluate_pair(first, second)`, which returns complete pair payloads
and ordered compound-heterozygous and autosomal-recessive endpoint evidence. It reuses the general
evaluator's pair finder, endpoint construction and recessive reduction. It omits de novo, dominant,
X-linked and summary calculations that this loop does not consume. General `evaluate()` and the
single-candidate evidence pass retain all models and summaries.

Both homozygous-recessive baselines remain necessary: decoded genotype calls can disagree with the
SQL row genotype. Even when no compound pair is formed, both autosomal-recessive outputs must be
retained. Pair confidence, endpoint quality caps, unknown affected status, warning order and the
strongest-score/last-equal-score reduction are unchanged. No storage, version-validation, eligibility,
work-budget or resource-limit change accompanies this optimization.

Three fresh-process synthetic comparisons against frozen evaluator and toolbox code from `83cabb3`
used the same 128-candidate, 8,128-pair workload and controlled provenance. Median inheritance time
fell from 3.40 seconds (3.38–3.41) to 2.45 seconds (2.45–2.62), about 28% less time. All complete pair,
evidence, membership, ranking and observation hashes matched. Database size remained 6,041,600 bytes;
sampled peak process RSS was approximately 205–209 MiB before and 209–216 MiB after. The separate
40-candidate comparison passed at 64 MB with exact matching outputs. These are synthetic evaluation
measurements, not a whole-case forecast. Historical private replays remain tied to their recorded
source-code identity and must not be relabeled as validation of a newer implementation.

### Synthetic analytical capacity trial

A synthetic ladder at source commit `661fc4d` exercised inheritance evidence, all 28 ranking
contexts, complete pair export, database checkpoint/close and research reporting. Three fresh
processes with 447 candidates in one gene (99,681 pairs) produced identical ordered evidence,
pair, membership, ranking and export hashes. Median supervised elapsed time was 40.75 seconds.
A 448-candidate workload (100,128 potential pairs) was refused without writing pair rows.

A separately approved experiment archived that same source and changed only its pair ceiling
to 200,000. Production remains at 100,000. Three fresh processes with 632 candidates (199,396
pairs) took 84.77–86.39 seconds, median 85.27 seconds, with sampled peak process-family RSS of
1.13–1.77 GiB. Complete hashes matched across repeats and were independently recomputed from
reopened read-only databases and saved pair exports. A two-gene workload (199,362 pairs) and
an affected-status case also completed. The 633-candidate workload (200,028 potential pairs)
was refused with zero saved pair rows. The existing database, RSS, elapsed-time, available-memory,
disk and accumulating-output guards passed throughout. Archived-source Git provenance is
`unknown`; a separate commit-pinned source manifest and file comparison establish that the
experimental cap was the sole source change. This is deliberately modified experimental code,
not verification of a production cap increase.

The fixtures use mixed phase, parental-origin and quality states, a mock plan, bundled synthetic
phenotype resources and complete exports. These measurements exclude annotation, validation,
assembly, production stage publication and resume. They do not establish whole-case capacity or
accuracy. Doubling pair work approximately doubled runtime; memory varied substantially between
processes. Retain the production ceiling and obtain target-workload counts and annotation/assembly
footprints before proposing another capacity increase. A larger ceiling postpones quadratic growth;
partial pair assessment would require a separate scientific-policy design.

## Remaining expansion gates

Use the sampler only within its existing cap. For disjoint coverage preparation, use the interface
below; another evenly spaced sample is not whole-input coverage. Keep live memory/disk limits and
the original inputs unchanged. Measure
actual annotation, candidate expansion, database/index overhead and pair workload before budgeting
whole-input processing. A bounded result cannot establish whole-case completeness, and decoy,
sex-chromosome and unannotated-contig records must remain recoverable with explicit limitations.

## Disjoint source preparation

`phase5 coverage-plan` streams a pinned single-sample VCF into a complete source-accounting plan.
It records every source record and alternate count in disjoint contiguous ordinal intervals, with
checksums, without copying the full VCF. Eligible runs are split at contig changes, deferred records
and a configurable 1–500 source-record boundary. A multiallelic record stays intact. Each materialized
record gains original `RDA_SOURCE_ROW` and `RDA_ALT_IDX` provenance; sample fields remain unchanged.

Supply a private JSON configuration matching `workflows.partitioning.CoverageInput`:

```json
{
  "source": "/private/path/original.vcf.gz",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "output": "/private/path/new-coverage-plan",
  "records_per_shard": 500,
  "confirmed_local_research_use": true
}
```

The digest above is a placeholder; supply the explicitly authorized input's actual digest locally.
From the checkout root, with the selected existing interpreter:

```bash
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 coverage-plan /private/path/config.json
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 coverage-materialize \
  /private/path/new-coverage-plan /private/path/new-partitions \
  --first 1 --last 1 --confirmed-local-research-use
```

The plan publishes atomically in a fresh directory. Materialization performs one sequential source
scan per request, with before/after input checksums, and writes only the requested range of at most
64 shards. This avoids one full scan per shard. `shard_000001/subset.vcf` and `receipt.json` are
committed through the existing immutable stage journal under a single-writer lock. Repeating a
request verifies committed shards without rewriting them. Interrupted uncommitted shards can be
recomputed. A changed source, plan, implementation or committed artifact is refused; use a fresh
plan/output instead of forcing resume. The journal is complete only after every planned eligible
shard has been materialized; that means preparation completeness, not annotation or analysis.

Current eligibility is limited to diploid sequence records on exact numeric autosomal names.
`chr` aliases, sex/PAR, mitochondrial, nonstandard, symbolic/nonsequence and non-diploid records
remain explicitly deferred and recoverable in the original input. Missing calls stay unknown.
Malformed records and ambiguous autosomal aliases fail closed. Eligibility does not verify local
reference/cache support: REF checking, normalization, annotation and genotype reconciliation remain
separate authorized operations. No chromosome renaming, inference, installation or download occurs.

Preparation uses bounded lines and headers, streaming metadata, a 100,000-shard planning cap and
sampled local resource checks: one hour, 1 GiB process RSS, 256 MiB minimum available memory,
25 GiB free-disk reserve, and 512 MiB output budget. The output budget includes existing materialized
artifacts on resume; it is a preparation safeguard, not permission to stage an entire genome.
Paths and derived summaries remain outside Git; CLI output includes only fixed success/error labels.

### Smaller increments after completed shards

An optional `shard_record_limits` mapping in `CoverageInput` can reduce the record limit for up to
eight explicitly numbered shards, for example `"shard_record_limits": {"5": 50}` with the default
500 records per shard. Every override must be an integer from 1 to the default limit, and its shard
must exist in the resulting plan. Defaults and the 500-record maximum remain unchanged. Contig and
eligibility boundaries may still make a shard shorter than its configured limit.

Use a fresh plan and materialization directory. Shards preceding the first override keep their
original boundaries and exact tagged source bytes when the source and default size are unchanged;
their annotations can therefore pass the normal pinned-reuse checks. The smaller shard covers the
next contiguous source records. Later shards continue immediately afterward using their configured
limits. No records are dropped, selected by score, or silently duplicated; the full source ledger
still accounts for all deferred and unprocessed records. Materialization validates each shard
against its specific limit and retains the existing immutable resume checks.

This permits a smaller, explicitly authorized next increment when pair workload limits expansion.
It does not predict unseen annotation growth, waive global pair preflight, or authorize new
annotation. All previously analyzed candidates must remain in a cumulative assembly; shrinking a
future increment must not be used to discard existing evidence to make a run pass.

## Verified global assembly and process isolation

`phase5 coverage-batch /private/path/batch.json` accepts a private
`workflows.coverage_batch.CoverageBatchInput` configuration. It names an existing source plan and
materialization directory, an explicit sorted list of **one to eight** shard numbers, two pinned
local tool specifications, a `Phase5Input` ranking specification, and a fresh external output.
It requires local research authorization, plus annotation-execution authorization for any shard
without an explicit reusable annotation. A command or configuration
file is not authorization to expand a previously approved subset. Materialization remains separate;
the coordinator neither chooses additional shards nor downloads tools or data.

Each pinned tool specifies its absolute launcher path, executable SHA-256, and existing `ToolJob`
configuration. Job input/output paths are templates: the fixed dispatcher replaces them only with
stage-owned paths. The normalization provider is bcftools; annotation is offline VEP/cache 116,
GRCh38, with the Track 1 profile. Both tools share the pinned local reference. This coordinator
requires one worker, buffer size one, at most 320 MiB tool RSS, 512 MiB tool output, and 1,800 seconds
per external operation. Existing adapters independently check executable version and reference
checksum. Cache path/release compatibility is checked; full cache-file supply-chain verification
remains an open gate.

For each requested shard, an annotation process normalizes and annotates, then exits. A separate
validation process reconciles source alternate ordinals and GT/GQ and imports every allele,
transcript consequence and allele/gene candidate. Separate assembly and ranking processes follow.
An isolated pair-work preflight runs between assembly and ranking. Each process runs with a
credential-free environment, disabled tracing, discarded stdout/stderr,
and a fixed typed dispatcher. Worker working directories are their private stage directories;
configuration paths must be absolute. Assembly's 256 MB, single-threaded DuckDB checks disable
spilling, so temporary variant tables cannot fall back to the repository directory. There is no
arbitrary command or SQL input and no live model.

The parent checkpoints each attempt's elapsed time, sampled family RSS, output size and exit status
under `attempts/`, including failures. Successful stage directories publish through the immutable
journal. Resuming an unchanged batch verifies and reuses committed annotation, validation and
assembly. A failed ranking stage is recomputed without rerunning annotation. A changed source,
configuration, implementation or committed artifact fails closed. Attempt receipts stay separate
from immutable scientific stages.

The supervisor requires 1 GiB available memory at each stage entry and enforces 3 GiB process-family
RSS, 2 GiB total batch output, one hour **per stage**, 256 MiB minimum live available memory and a
25 GiB free-disk reserve. Limits stop processing; they do not justify dropping candidates. The
one-hour stage cap is not a whole-batch runtime estimate. Existing pair-work limits remain unchanged.

Validation binds actual tagged source rows to the coverage segment digest and matches the
annotation receipt chain. Assembly requires compatible source plan, sample identity, reference,
annotation context and ingestion implementation. It streams all four tables in source-interval
order, retains every transcript, and rejects overlapping intervals, duplicate source alleles,
duplicate normalized identities and duplicate candidate IDs. Duplicate normalized alleles require
an explicit source mapping; they are never silently removed.

The resulting `assembly/` is a checksummed Phase 5 input. Set the ranking specification's
`annotation_rehearsal` to `<batch-output>/assembly` and `output` to `<batch-output>/ranking/run`.
Phase 5 copies verified tables without repeating ingestion, then computes evidence, cross-shard
same-gene/chromosome pairs and all ranking contexts globally. It never concatenates shard-local
rankings. Coverage records selected and unprocessed source records/alternates plus all original
dispositions; whole-genome analysis and causal accuracy remain unestablished.

Synthetic regressions compare assembled allele, transcript and candidate tables with an
unpartitioned control, exercise cross-shard pair evidence in the actual global workflow, reject
mixed or corrupted inputs, and resume after ranking failure without repeating annotation.
The next private expansion should be a small, explicitly scoped multi-shard pilot, followed by
review of measured annotation cost, candidate/transcript expansion, pair workload and disk use.
Eight shards is a hard request cap, not a recommendation to run eight immediately.

### Reusing completed annotations in a fresh batch

The optional `reuse_annotations` mapping selects sealed annotation bundles by requested shard
number. For example, the following fragment belongs in the private batch configuration:

```json
{
  "reuse_annotations": {
    "1": {
      "directory": "/private/path/earlier-batch/annotation_000001",
      "integrity_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }
}
```

Replace the placeholder with the SHA-256 of that bundle's `integrity_manifest.json`. Reuse verifies
the complete inventory, original source checksum, exact tagged subset, receipt input/output chain,
executable checksum and full tool configuration except the old input/output locations. It rejects
symbolic links, overlapping input/output directories, extra artifacts and changed pins. The stage
copies files into the new batch and checks both copies and originals again before publication;
it never hard-links or changes the original bundle. Its origin and pin remain in batch identity.

Reuse saves external normalization and annotation. Validation, genotype reconciliation, ingestion,
global assembly and ranking run with the current implementation. Historical validation proofs or
rankings are not imported, and code-bound resume checks remain unchanged. This permits an older
annotation bundle to be used with a new implementation without pretending that its old analysis
was produced by the new code. Existing tool/reference checks and cache limitations still apply.

If every requested shard has a reuse entry, `confirmed_annotation_execution` may be false. A mixed
batch still requires authorization for new annotation; it executes tools only for unlisted shards.
Local research authorization remains mandatory. These fields record authorization; setting them
does not grant permission to expand the approved private scope.

### Global pair-work preflight

The committed `pair_preflight/pair_workload.json` binds an exact count to the assembled input's
integrity checksum. Counting uses the same function as inheritance execution: heterozygous
candidates with assigned genes are grouped by case-insensitive gene and chromosome, then each
group contributes `n*(n-1)/2`. Unknown gene placeholders and NULL chromosomes cannot define pairs.
Gene whitespace is not silently normalized for grouping. Integer arithmetic avoids rounding;
no pairs or pair payloads are materialized during counting.

The preflight considers all assembled candidates, including cross-shard combinations, because the
conservative branch retains the full source. The report stays private and records whether the
unchanged 100,000-pair budget is satisfied. A larger count commits the report but prevents ranking
from starting, including on resume. Annotation, validation and assembly remain available. It never
filters candidates or changes the cap to make the run pass. Execution also retains its own
branch-specific budget check. The count measures enumeration work, not retained evidence, causal
accuracy, runtime or memory guarantees; the existing live resource supervisor remains mandatory.
