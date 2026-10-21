# Reproducing the bounded Track 1 workflow

The public code provides a route from an authorized local source VCF and prepared resources
to a bounded analysis, deterministic hypothesis selection, and final CSV. It also permits
selection to be reproduced from a pinned completed corpus without repeating its scientific
analysis. All real inputs, configurations, intermediate artifacts, receipts, and outputs stay
outside Git. This is research software; completion does not establish clinical accuracy,
whole-case coverage, or organizer acceptance.

Three claims must remain separate:

| Claim | Evidence required |
| --- | --- |
| Code and configuration availability | A published revision containing the commands and data-free examples below. |
| Exact private provenance | Authentic source, resource, configuration, code, journal, and artifact identities retained locally. Public examples cannot supply these. |
| Empirical reproduction | A recorded execution and comparison at the claimed starting stage and scope. Export equality alone is not a raw-input-to-final replay. |

This guide documents the available code path. It does not report that a complete raw-input
replay of a particular private package has been performed. Historical analyses keep their
original code and configuration identities; new code must not be attributed to an older run.

## Prepare the environment and local records

Use an existing supported Python environment with the dependencies described in the
[README](../README.md#install-and-run-a-synthetic-example). Commands below run from the repository
root with `.venv/bin/python`; replace that path consistently if another environment was selected.
`PYTHONPATH=src` selects this checkout. No command below installs dependencies or downloads
reference resources. Follow the [resource workflow](resources.md) separately when acquiring or
importing authorized public resources.
Create an analysis from a clean checkout of the intended committed revision. The selector
requires the same clean analysis commit recorded throughout its saved journal chain; publishing
or committing newer code later cannot repair an earlier incomplete software identity.

Before executing a private workflow, supply authentic local records for:

- The original single-sample VCF and its index, source digest and dataset revision, selected
  source scope, and authorization for local research use.
- The supplied phenotype document containing explicit positive HPO table identifiers and the
  verified normalized HPO resource directory with its release locks, checksums, and receipts.
- The existing bcftools and VEP executable paths, exact versions and hashes, GRCh38 reference
  identity, and compatible offline VEP 116 cache. The batch/corpus contracts require the Track 1
  annotation profile and pinned shared reference. Preserve the actual normalization data-version
  declaration and all tool settings from receipts when reusing annotation.
- The saved affected-status and linkage declarations and their independently supported basis.
  Do not infer sample identity, sex, parentage, phenotype linkage, or affected status. In this
  entry point `confirmed_affected_sample=false` means unknown. A true saved flag must not be
  rewritten as false merely because its basis remains unconfirmed; disclose that uncertainty.
- Separate fresh output directories and disabled external tracing. Keep public reference caches
  separate from restricted source and derived data. The supervisor must be able to inspect local
  processes and enforce its resource limits.

The complete examples are
[`track1-coverage-plan.json.example`](../configs/track1-coverage-plan.json.example),
[`track1-coverage-batch.json.example`](../configs/track1-coverage-batch.json.example), and
[`track1-corpus.json.example`](../configs/track1-corpus.json.example).
Copy them into restricted storage and replace every placeholder. Their authorization booleans
start false; change them only to record authorization actually given. The example affected-status
value is unknown, not a claim about an existing analysis. Populate it from the intended supplied
context, or preserve the actual saved value when reproducing a historical configuration.
Do not guess missing releases, hashes, sample mappings, HPO terms, or authorization.
Existing receipts pin declared tools and resources, but they do not establish a complete
checksum inventory of every cache file or every dependency invoked by a launcher. Preserve
that limitation when describing historical reproducibility; do not invent missing pins.

The authoritative schemas are `CoverageInput` in
[`partitioning.py`](../src/rare_disease_agent/workflows/partitioning.py), `CoverageBatchInput` and
`PinnedTool` in [`coverage_batch.py`](../src/rare_disease_agent/workflows/coverage_batch.py),
`CorpusInput` in [`corpus.py`](../src/rare_disease_agent/workflows/corpus.py), `Phase5Input` in
[`phase5.py`](../src/rare_disease_agent/workflows/phase5.py), and `ToolJob` in
[`adapters.py`](../src/rare_disease_agent/tools/variants/adapters.py). The examples include all
required fields. Job input/output paths are templates replaced by stage-owned paths; they do
not select arbitrary commands. An existing environment or available executable is not permission
to process private data or execute annotation.

## Account for the source and materialize the approved scope

After completing the private source-plan configuration, run:

```sh
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 coverage-plan \
  /absolute/restricted/path/coverage-plan.json
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 coverage-materialize \
  /absolute/restricted/path/source-plan /absolute/restricted/path/partitions \
  --first 1 --last 1 --confirmed-local-research-use
```

The one-shard range illustrates a small authorized scope; it is not a prescription for a
particular case. Match the configured shard size and selected range to the approved scope.
Planning accounts for the complete source; materialization copies only the requested supported
records. Current eligibility is diploid sequence records on exact numeric autosomal names.
Other records remain deferred and recoverable. Neither operation annotates or ranks variants.
See [disjoint source preparation](phase5-scaling.md#disjoint-source-preparation) for all admission
and resource bounds. Keep selected, unprocessed, and deferred records distinguishable.

## Create or reuse sealed annotations, then analyze one corpus

If annotation bundles do not already exist, complete the private batch configuration and run
this command only within explicit normalization/annotation-execution authorization:

```sh
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 coverage-batch \
  /absolute/restricted/path/coverage-batch.json
```

Set `shards` to the approved materialized shard numbers. The batch performs normalization,
offline annotation, validation, assembly, pair preflight, and global analysis; it preserves
completed annotation bundles under `annotation_000001/` and analogous numbered directories.
Its `ranking.annotation_rehearsal` must equal `<batch-output>/assembly`, and `ranking.output`
must equal `<batch-output>/ranking/run`. A fully reused batch can omit new tool execution using
explicit pinned `reuse_annotations`; see [annotation reuse](phase5-scaling.md#reusing-completed-annotations-in-a-fresh-batch).

For the selector below, produce a completed **corpus** directory. A coverage-batch directory
is not a substitute. Use the new batch's sealed annotations or existing authentic sealed bundles
that pass the same source and resource checks. Create a private JSONL reference manifest with
one object per selected bundle:

```json
{"shard": 1, "directory": "/absolute/restricted/path/new-annotation-batch/annotation_000001", "integrity_sha256": "REPLACE_WITH_VERIFIED_ANNOTATION_SEAL_SHA256"}
```

Bind each entry to its verified `integrity_manifest.json`, then put the actual manifest digest
in `reference_manifest_sha256` in the private corpus configuration. A newly calculated hash
identifies bytes; it does not establish the authenticity of untrusted inputs. Reuse requires the
original tagged source subset and full annotation receipt chain, including executable and
resource identities. Keep referenced bundles immutable and available.

Set `ranking.annotation_rehearsal` to `<corpus-output>/assembly` and `ranking.output` to
`<corpus-output>/ranking/run`, then run:

```sh
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 corpus-run \
  /absolute/restricted/path/corpus.json
```

Corpus execution performs current-code validation and ingestion, global assembly, exact
pair-work preflight, deterministic evidence computation, ranking, and reporting. It does not
execute annotation. It retains all transcript consequences, source mappings, conservative/rescue
candidates, and eligible cross-shard pairs. It does not concatenate shard rankings. The full
global pair-work limit and resource supervision still apply; a refusal preserves committed
work and does not authorize dropping candidates or raising a cap.

Current-code source plans and materialization are required. Changed implementation, inputs,
resources, or committed artifacts require fresh preparation/output, with pinned annotation
reuse only where the exact source and receipt chain remain compatible. See the
[corpus contract](phase5-scaling.md#referenced-annotation-corpus) and
[execution integrity rules](phase5-execution.md#integrity-and-representation).

## Select hypotheses from a pinned completed corpus

[`scripts/track1_select.py`](../scripts/track1_select.py) makes the saved-analysis selection
stage available without a local launcher or private adapter source. It consumes the existing
completed corpus and recovers immutable ingestion, source mappings, and saved evidence through
the pinned journal chain. It does not create annotations, recompute scientific evidence,
rerank candidates, or expand coverage.

Copy [`track1-select.json.example`](../configs/track1-select.json.example) into restricted storage
and supply:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. |
| `corpus_directory` | Absolute path to the completed corpus root. |
| `corpus_journal_sha256` | Trusted expected SHA-256 of that corpus's `run.json`. |
| `expected_analysis_head` | Exact 40-character clean Git commit recorded in the saved analysis journals, not the current selector's commit. |
| `expected_proband` | Explicit submission label; this does not establish biological sample identity. |
| `output_directory` | Fresh absolute path outside Git, separate from the corpus; its parent must exist. |
| `confirmed_local_research_use` | `true` only when local use is authorized. |

Use verified local receipts to supply the journal and code pins. Historical code may differ
from the current selector implementation; retain both identities. Paths must be canonical and
must not traverse symbolic links. Run:

```sh
PYTHONPATH=src .venv/bin/python scripts/track1_select.py \
  --config /absolute/restricted/path/select.json
```

The selector applies the [frozen policy](../scripts/track1_submission_policy.py) to the first
100 distinct source-allele anchors in `filtering_phenotype_inheritance` order, considers the
complete saved pair stream within its existing work bound, and emits at most ten hypotheses.
Pair partners can lie beyond the anchor window. Selection and the subjective rank-based EPCR
allocation are reproduced from saved evidence; phase and scientific uncertainty remain intact.
The [methods template](track1-methods-template.md) gives the singleton/pair rules and EPCR formula.

Success atomically publishes `draft.csv`, `audit.json`, `manifest.json`, and
`integrity_manifest.json` in the new private directory. The audit records provenance, selection
decisions, and limitations. Preserve its seal with the configuration and source identities.
These patient-derived files must not be placed in Git or public logs.

## Format the final CSV and document the execution

Complete [`track1-export.json.example`](../configs/track1-export.json.example) with the selector's
output directory and its verified integrity-manifest digest, then run:

```sh
.venv/bin/python scripts/track1_export.py --config /absolute/restricted/path/export.json
```

The [final exporter](track1-export.md) verifies the sealed selection and maps supported bare
nuclear chromosome labels to `chr` presentation. It preserves endpoints, positions, alleles,
row order, EPCR, finding types, and notes. It publishes `submission.csv`, `export_audit.json`,
and a new integrity manifest. It does not repeat selection or upstream analysis.

Complete the [methods report](track1-methods-template.md) from verified private records.
Record the source scope, code identities for every stage, exact resources and configuration,
actual saved flags and their independent basis, and the stages actually rerun and compared.
Keep code publication, private provenance retention, synthetic correctness checks, and an
empirical full replay separate. Include the organizer-prescribed acknowledgement outside the
abstract. Final local review and organizer upload remain separate actions.
