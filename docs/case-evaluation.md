# Local labeled-case failure analysis

`phase5 evaluate` reads existing Phase 5 artifacts and explicit truth labels. It does not run
annotation, inference, filtering or ranking, change weights, resume a run, or retrieve labels.
This supplies an evaluation path; it does not establish independent or official accuracy.

From the repository root, using the selected existing interpreter:

```bash
PYTHONPATH=src .venv/bin/python -m rare_disease_agent phase5 evaluate \
  /private/path/truth.json /private/path/new-evaluation
```

The manifest, run directories and output must be outside every Git checkout. The output must be
fresh and separate from every input run. The command prints only success or a sanitized exception
class, never case values or metrics. Both JSON and TSV results remain private, including aggregates.
External tracing must be disabled. No new data/resource download is authorized by this command.

## Explicit truth manifest

This example is entirely synthetic. Replace its placeholders locally using legitimately available
labels and reviewed source mappings; do not guess hidden challenge truth.

```json
{
  "schema_version": 1,
  "confirmed_local_research_use": true,
  "mode": "filtering_phenotype_inheritance",
  "cases": [
    {
      "case_id": "synthetic-example",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "truth_reference": "invented example; replace with label provenance",
      "expected_gene_id": "SYNTHETIC_GENE_ID",
      "alleles": [{"source_row": 1, "source_alt": 1}],
      "run_directory": "/private/path/existing-phase5-run"
    }
  ]
}
```

- `source_sha256` must match the original VCF checksum in the run journal, not its annotated subset.
- `source_row` is the one-based original data-record ordinal, excluding headers. `source_alt` is
  the one-based original alternate ordinal. These match `RDA_SOURCE_ROW`/`RDA_ALT_IDX`, including
  across normalization. Coordinate-to-source mapping is an explicit upstream review step; this
  evaluator neither reads the original VCF nor infers coordinate equivalence.
- `expected_gene_id` is the exact `gene_id` namespace in `candidate_map.parquet` (normally the
  VEP gene identifier, with the importer's documented fallbacks). It is not a fuzzy symbol match.
- Omit `alleles` or supply `[]` for gene-only truth. Supply one allele for a single-variant label
  or two distinct alleles when both are required. Alternative causal hypotheses, multiple diagnoses,
  and truth involving more than two alleles require a separate evaluation contract.
- For a case without a usable run, set `run_directory` to `null` and supply `unavailable_reason`:
  `not_run`, `preparation_failed`, `annotation_failed`, `execution_failed`, or `unsupported_input`.
  These are operator-supplied categories, not diagnoses inferred by the evaluator. Include such
  cases in the frozen manifest so preparation failures cannot disappear from the denominator.
- An existing incomplete run can be inspected using its directory. Only committed ingestion
  artifacts contribute retention observations; incomplete runs receive no final retrieval rank.
- Case IDs and original source checksums must be unique. This prevents counting renamed copies;
  it does not establish independence between relatives, reused genes or templates. Curate those
  relationships and separate tuning/evaluation sets before interpreting accuracy.

`mode` selects one existing persisted mode: the four default ablations, or an ablation prefixed
with one of the six Phase 5 strategy names. A missing mode is an error, not an empty ranking.
The default mode uses the pipeline's original weights. The separately named `baseline_*` strategy
has different fixed weights; do not conflate these modes when comparing results.

## Reports and interpretation

The atomic output directory contains `evaluation.json` and `cases.tsv`. Each case records its
expected gene/alleles, completion status, ranked candidate count, retrieval ranks, and per-target
retention, annotation availability, evidence warnings, branch membership and score contributions.
The best candidate for the expected gene is also recorded, including for gene-only labels.
Nested evidence is serialized as JSON in the TSV columns.

Three separate MRR and Recall@1/5/10 groups are reported:

1. **Gene:** deduplicate the persisted candidate order by first appearance of each assigned gene ID.
2. **Allele:** deduplicate by source allele; any gene candidate can establish allele retrieval.
3. **Allele/gene:** use the persisted rank of the candidate linking the labeled allele and gene.

For two-allele truth, both must be retrieved; case rank is the worse of their ranks. A missing allele
makes the case a miss even when the other allele or the expected gene ranks first. Gene-only cases
remain in gene metrics and are explicitly excluded from allele denominators. Unavailable and
incomplete labeled cases count as misses: these are end-to-end retrieval metrics, including coverage
failures, not accuracy conditional on successful runs. No metric establishes whole-case completeness.

`retained_after_preparation` means observed in the committed imported allele ledger. It is unknown
without committed ingestion. Absence is reported as `outside_ingested_subset`; it does not establish
whether selection, upstream preparation, or an incorrect supplied label caused the absence.
Deferred contigs and missing expected gene mappings have separate reasons. After candidate mapping,
active ensemble membership (or conservative membership for `conservative_*`) determines filtering
retention; a mismatch with ranking membership is refused. Unmapped/deferred candidates have unknown
filtering retention because they never reached that candidate stage.

Contributions are reconstructed from each persisted feature vector and its recorded weights and
must reproduce its stored score. Missing phenotype associations remain distinct from a numeric zero.
Inheritance records can exist despite absent family evidence; their warnings remain inspectable.
Strong-model lists are descriptive and do not label overlapping models as false positives.
`rescue_member` records membership; `rescue_required` stays unknown without a counterfactual run.

## Integrity and limits

The evaluator checks the journal's configuration hash, truth/source binding, and each consumed
artifact against its committed-stage checksum. It checks those files again after each case read,
and retains their hashes along with the manifest, pipeline/resource identities and evaluator code
hash. It does not verify unrelated stage files or reopen references, annotations or original inputs;
this is not a replacement for complete run-integrity verification. Symlinked consumed artifacts,
mixed run identities and ambiguous ranks/mappings are refused. Any integrity/schema/read failure
aborts publication rather than silently removing a case or claiming a biological miss.

Metadata is bounded to 4 MiB, manifests to 1,000 cases and two alleles per case. Evaluation requires
at least 1 GiB live available memory at entry. DuckDB uses one thread and a 256 MB memory limit;
disk spilling and automatic extension installation are disabled. Run one evaluation at a time and
keep inputs immutable. The evaluator does not invoke the annotation or Phase 5 execution supervisor.

Coverage of the evaluation mechanism uses deliberately synthetic artifacts, including real synthetic
Phase 5 output. Before a real-case comparison, freeze the manifest, verify label provenance and
sample linkage, confirm representation support, and preserve both the completed run and evaluation.
Run the same frozen labels against a separate run for a controlled change; compare ranks, retention,
features, resource identities and warnings without tuning against the final evaluation set.
