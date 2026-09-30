# Recommended Phase 6 — Track 1 robustness and review readiness

Status: proposed, not started. Phase 5 must complete first. This plan contains no patient-derived
results and authorizes neither additional downloads nor submission.

Track 1 is the current priority. Targeted correctness work may proceed while the Phase 5
completion evidence is being located; it does not satisfy the entry gate or start a private run.
The [evaluator-v2 synthetic comparison](phase4.md#track-1-evidence-sufficiency-correction-2026-09-29)
is one such correction, not independent accuracy validation.

## Objective

Establish how stable, reproducible and reviewable the completed deterministic Track 1 workflow is.
Produce a versioned release candidate and a privately reviewable submission export. Keep clinical
interpretation, Track 2, raw sequencing, drug repurposing and cloud deployment outside this phase.

## Entry gate: finish Phase 5

Require an integrity-verified local run from authorized inputs through phenotype normalization,
variant ingestion, supported inheritance analysis, all candidate branches, deterministic rankings,
and private reports. Record versions, resource checksums, runtime, peak memory, disk use and unresolved
coverage. Demonstrate safe failure/resume and pass synthetic tests and privacy checks. A successful
annotation sample is not this end-to-end result. Missing pedigree facts must remain uncertain.

## Recommended work

Before treating a bounded Phase 5 rehearsal as a whole-case result, define a coverage-expansion
plan: benchmark larger autosomal batches, account for every original record, validate sex/PAR and
nonstandard-contig handling, and establish feasible annotation/pair-analysis budgets. Keep deferred
records recoverable. A subset ranking must never be submitted as a complete case ranking.

1. Freeze the completed Phase 5 configuration and reference versions. Preserve that run unchanged.
   Replay into a new private directory and compare candidate identities, features, branch membership
   and scores. Exclude timestamps and other explicitly nondeterministic metadata from comparisons.
2. Expand synthetic regression cases for multiallelic normalization, overlapping genes/transcripts,
   phased and unphased genotypes, absent parents, missing frequencies, obsolete HPO terms and
   unsupported contigs. Verify that unknown evidence stays distinct from negative evidence and that
   conservative and rescue branches survive.
3. Exercise interruption and resume at ingestion, annotation, scoring and report boundaries. Confirm
   failed staging is never treated as a completed run and changed inputs invalidate resume. Measure
   practical memory, disk and runtime limits on the development machine.
4. Measure sensitivity to phenotype omissions, missing evidence and documented ranking weights.
   Record branch survival and candidate overlap. Perturb public/synthetic cases first; any authorized
   patient-derived sensitivity results remain private. Stability does not establish causal correctness.
5. Evaluate ranking accuracy on legitimately available labeled cases using a fixed, separate evaluation
   set. Report case counts and uncertainty with MRR/top-k recall; keep tuning cases separate. If no
   suitable labels are available, mark accuracy evaluation pending rather than inferring it from
   stability or probing hidden challenge answers. New datasets require explicit download approval.
6. Validate the private export against the official challenge format once its current specification
   is available. Perform local schema/round-trip checks. Prepare a review packet documenting evidence,
   uncertainty and coverage; official submission requires explicit user action or authorization.
7. Run the full offline synthetic suite and privacy guard. Prepare a software-only release candidate,
   reproducibility instructions and limitations. Keep all patient artifacts, including aggregate
   patient results, outside Git. Publishing or submission is a separate action.

## Accuracy evaluation decisions before tuning

First establish coverage and a frozen, independent labeled evaluation set. Do not optimize the
ranking weights against the bundled templates or the challenge case. Separate tuning and final
evaluation cases, account for related families and reused variants/genes, and record knowledge
resource versions so overlap can be assessed. Report variant and gene retrieval separately, and
require both causal alleles when evaluating a labeled compound-heterozygous pair. Include excluded,
unsupported and failed cases in coverage accounting rather than reporting only successful runs.

Use the same cases and resource scope for a comparison with an established offline prioritizer
before introducing a more complex ranking model. [PhEval](https://monarch-initiative.github.io/pheval/)
provides a reusable framework that separates corpus preparation, tool execution and analysis;
assess its fit before building another benchmark framework. Its
[Exomiser integration](https://github.com/monarch-initiative/pheval.exomiser) is a candidate comparator.
These are proposed integrations, not installed dependencies or authorized resource downloads.

Accept a ranking change only with a documented reason, preserved scientific invariants, and
paired evaluation against the frozen baseline. Report regressions and uncertainty alongside gains.
Without independent labels, report correctness and robustness improvements without claiming
improved real-case accuracy.

## Optional experiment after the deterministic baseline

A single local 7B–8B quantized planner may be evaluated only after separate model-download and
patient-summary-use authorization, a license review and live hardware checks. Start with synthetic
inputs. Compare tool-call validity, repeated decisions, candidate/branch overlap, runtime and peak
memory with the deterministic baseline. Numeric scores remain deterministic. No remote model or
patient-bearing tracing is introduced. This experiment is not a Phase 6 completion requirement.

## Deliverables and exit criteria

- Versioned software regression and replay harnesses with synthetic-only fixtures.
- Private replay, robustness and resource-use results with complete provenance and checksums.
- Labeled-case evaluation where supported, or an explicit outstanding evidence limitation.
- Private, locally validated export and review packet; no automatic challenge submission.
- Passing offline tests/privacy checks and a release-candidate checklist identifying remaining limits.

Start Phase 6 only after reviewing the Phase 5 completion evidence. This recommendation does not
implement Phase 6 or expand the current authorization to full patient annotation/ranking.
