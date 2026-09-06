# Phase 4: reproducible local research pipeline

Phase 4 hardens Track 1 around explicit public-resource acquisition, persistent evidence,
fail-closed genomic validation, bounded established-tool adapters, synthetic calibration,
a deterministic critic, local redacted reports and stage-level recovery. Literature RAG,
external biomedical LLMs, Track 2, frontend/cloud work, clinical use and official submissions
remain deferred.

## Deterministic boundaries

The planner selects a typed evidence operation. Deterministic tools execute it, persist versioned
evidence and create reversible branches. The ranker calculates inspectable quality, rarity,
consequence, phenotype and inheritance features. Neither the planner nor the critic can author SQL,
launch shell commands, invent annotations, perform ranking math or delete rescue branches.

Phenotype/inheritance evidence writes are transactional and bounded to batches; mixed data versions
and run IDs are rejected. Complete evidence is exported as Parquet. Ranked results use SQL ordering
with deterministic identifier tie breaks. Aggregate statistics and branch selection also stay in
DuckDB. Python and graph state retain bounded samples and summaries, not complete score maps or
inheritance lists. Complete private JSON artifacts are written incrementally for inspection.

The critic checks evidence completeness, run/release provenance, competing strong models, rescue
survival and uncertainty language, with at most two typed read-only re-inspections. A critical
failure prevents successful report publication. Competing models are reported as research-review
findings; they do not change scores. The report separates feature values, phenotype association
and match counts, inheritance fits, quality flags, uncertainty notes and release citations.
It explicitly prohibits clinical use and official submission.

## Reproducible evaluation

```bash
rare-disease-agent benchmark-phase4 --output-dir /tmp/phase4-benchmark --seed 17
```

The six Phase 3 cases are the calibration set. Seven additional named cases form the evaluation
set. The split and seed are written before execution. Two configurable weight sets are compared
on calibration MRR; ties are resolved by configuration name. Selection is saved before any held-out
case is evaluated. Evaluation data cannot change the selected weights. All tests and benchmarks
use synthetic data and the mock LLM backend.

The selected baseline weights are quality 0.10, rarity 0.20, consequence 0.15, phenotype 0.30 and
inheritance 0.25. These are architecture benchmarks, not independently validated clinical weights.
The named split is disjoint, but cases deliberately reuse synthetic templates and causal variants.
There is no claim of population-level generalization or leakage-free clinical validation.

| Evaluation case | Causal rank | Filtering only | + Phenotype | + Inheritance | Both |
|---|---:|---:|---:|---:|---:|
| Incomplete penetrance | 1 | 4 | 1 | 4 | 1 |
| Affected sibling | 1 | 4 | 1 | 1 | 1 |
| Missing parent | 1 | 4 | 1 | 1 | 1 |
| Female X uncertainty | 1 | 4 | 1 | 4 | 1 |
| Unphased compound heterozygosity | 2 | 5 | 2 | 2 | 2 |
| Mosaic / low alternate fraction | 1 | 4 | 1 | 5 | 1 |
| Annotation missingness | 3 | 6 | 6 | 5 | 3 |

MRR is **0.8333**; top-1 recall **5/7 (71.4%)**; top-5 and top-10 recall **7/7**.
Final-branch causal recall and novel-gene rescue membership recall are both **100%**. Per-branch
causal survival is persisted separately. The seeded 1,000-resample case bootstrap produces MRR
95% interval **[0.6429, 1.0000]** and top-1 **[0.4286, 1.0000]**. All-success top-5/10 bootstrap
intervals collapse to [1,1]; that reflects this tiny fixture set, not certainty about future cases.

The expected inheritance model is strongly identified in **2/7** cases. Five cases intentionally
require uncertainty rather than a strong call; all five receive subthreshold expected-model fit.
There are **four other strong model calls** across the seven cases, counted as false positives
under the single-label synthetic truth convention. Dominant and de novo models can overlap, so
this is a strict model-specific regression diagnostic rather than mutually exclusive disease
classification accuracy. Expected-model uncertainty classification is **7/7**. The uncertainty
Brier diagnostic is **0.1710**, using `1 - heuristic_fit` as a proxy; it is not calibrated probability.

Run artifacts retain every case's rank, runtime, RSS, branch survival, false positives, uncertainty
metrics, four ranking ablations, phenotype-removal ablation and software/data provenance. Runtime
and memory measurements for the final run are recorded in [Phase 4 validation](phase4-validation.md).

## Recovery and authorized-data scope

Repeated runs verify source/code/resource/configuration identity and each committed stage's
artifacts. Completed stage audits and membership remain unchanged. Interrupted, uncommitted stages
are recomputed, with no duplicate committed events. A self-contained persisted variant table avoids
references to staging paths after publication. Filesystem locks prevent concurrent workflow writers.

The synthetic CLI demonstrates recovery without accessing patient data. A separate typed
`authorized_dry_run` API requires explicit confirmation of exact input/output scope, checksum-pinned
prepared Parquet, typed genomic context and verified HPO resources. Both input and derived output
must be outside Git. This interface was tested only with synthetic temporary files; no real-data
path was authorized, inspected or processed.

See [resource lifecycle](resources.md), [operational procedures](operations.md),
[privacy](privacy.md) and [architecture](architecture.md).

## Scientific and operational limits

- Full official public releases were not downloaded; production-shaped parsing is verified with
  tiny official-schema synthetic fixtures. Source licenses are recorded, not interpreted as grants.
- Ontology memory depends on resource size; per-patient evidence is batched and externalized.
  Compound pairing is quadratic within a gene and fails above a 100,000-pair work budget.
- Sex-chromosome/PAR context and normalization/reference declarations must be supplied correctly.
  Symbolic alleles, unsupported contigs and ambiguous ploidy fail closed. This is not a full VCF
  specification implementation, transcript annotator or reference-sequence validator.
- Female X inheritance and low allele fraction remain uncertain baseline models. Penetrance,
  mosaicism, structural variation, population-specific priors and clinical interpretation are not
  established by these synthetic tests. Same-parent compound pairs are explicitly unconfirmed.
- bcftools/VEP execution is preview-first and mocked in tests. No tool/cache was installed and no
  actual annotation job ran. Full cache supply-chain validation and installation-specific behavior
  require a separately authorized rehearsal. Process resource limits use polling, not an OS sandbox.
- Reports are redacted local research projections, not anonymous public artifacts or diagnoses.
  Complete evidence remains sensitive. Recovery resumes stages, not individual graph decisions.

## Recommended Phase 5

Commission an explicitly authorized, independently held-out public/consented-data validation study:
verify real pinned annotation/resource installations, curate independent truth splits, refine
uncertainty and inheritance models against those labels, and evaluate failure/coverage behavior
before considering any expansion of scope. Phase 5 is not implemented automatically.
