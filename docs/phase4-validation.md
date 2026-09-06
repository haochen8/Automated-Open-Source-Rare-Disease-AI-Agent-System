# Phase 4 validation record — 2026-09-05

Baseline: `0088e23cb5f0444ecca03a9e03c0ec8deb2eda64`, clean checkout before changes.
Baseline gates: 91 tests, 87% coverage, Ruff check/format, privacy, dependency and diff checks passed.

Final verification uses Python 3.13.7 on macOS 26.6.2 with 16 GiB RAM. The executing Python reports
**x86_64**, even though the requested hardware profile is Apple M2. These are measured development
results, not native arm64 performance claims. No CUDA, model weights, live inference, full public
resource download, real patient input, tool installation or actual annotation execution was used.

## Test and build gates

The completion suite contains **150 offline tests** spanning all original Phase 0–3 tests plus
resource URLs/redirects/checksums/conversion, HPO-to-ranking flow, large generated evidence,
rollback/mixed-version refusal, portable membership, genomic/pedigree validation, mocked adapters,
all seven inheritance/missingness cases, critic boundaries, report redaction, authorized synthetic
rehearsal, stage interruption/resume, and calibration/evaluation ordering. The initial completion run
passed all 150 tests with **88% branch-aware coverage in 35.68 seconds**. Verification after resuming
on 2026-09-06 passed the same 150 tests with 88% coverage in 35.14 seconds; Ruff, privacy, dependency,
package build, and diff checks also passed again.

Required checks: full pytest with branch coverage; Ruff check and format check; privacy guard;
`pip check`; Hatchling wheel and sdist build; `git diff --check`; complete staged-file inspection.
CI installs development dependencies, runs the same static/build gates and refuses test-network
connections. Build output is ignored and excluded from the commit.

## Final synthetic benchmark

Executed with seed 17, two weight configurations, six calibration and seven held-out named cases.
Baseline calibration MRR: 0.8333. Phenotype-emphasis calibration MRR: 0.7833. The baseline was selected
before evaluation. Evaluation MRR: 0.8333; top-1: 5/7; top-5/10: 7/7; final branch and actual
novel-gene rescue membership recall: 100%. Expected-model strong-call accuracy: 2/7;
expected-model uncertainty classification: 7/7; four competing strong model calls; uncertainty
Brier proxy: 0.1710. Interpretation and limitations are in [phase4.md](phase4.md).

Each held-out case took **0.595–0.613 seconds** in the final reviewed run; end RSS was
**220.91–223.80 MiB**. These case runtimes include validation, filtering, evidence, ranking, ablations,
critic and report. They are not clinical or full-genome throughput estimates.

The generated stress comparison ran both sizes sequentially in the same Python process:

| Variants | Persistent evidence rows | Max Python batch | Observation bytes | Returned ranks | Initial RSS MiB | Peak RSS MiB | Seconds |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2,000 | 10,100 | 256 | 1,986 | 20 | 75.73 | 203.57 | 1.13 |
| 20,000 | 100,100 | 256 | 1,990 | 20 | 205.52 | 580.93 | 10.86 |

The larger run starts with allocator/library memory retained from the first. Peak RSS measures the
whole process; SQL storage/indexing can grow within the configured 1 GiB DuckDB limit. Python
batches and planner observations remain bounded as row count grows. Generated calls are reference
backgrounds across 100 synthetic genes; dense compound-pair behavior is bounded by a separate
100,000-pair refusal policy, not represented as stress throughput in this table.

## Reproduction

```bash
rare-disease-agent benchmark-phase4 --output-dir /tmp/fresh-phase4-benchmark --seed 17
python -c "from pathlib import Path; from rare_disease_agent.synthetic.stress import stress_benchmark; print(stress_benchmark(Path('/tmp/fresh-phase4-stress'), count=20000))"
```

Use fresh directories for changed code/configurations. The recorded run wrote local artifacts under
`/tmp/phase4-completed-release` and `/tmp/phase4-final-stress-{2000,20000}`. These temporary directories
were no longer present when work resumed on 2026-09-06; rerun the commands above to regenerate
artifacts. Each run embeds resource and software provenance. Only this synthetic aggregate
validation record is committed.
