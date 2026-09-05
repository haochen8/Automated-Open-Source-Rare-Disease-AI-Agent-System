# Recommended Phase 4 plan

Phase 4 should harden Track 1 for authorized real-data dry runs without adding Track 2:

1. Add a controlled public-resource manager that pins and verifies full HPO releases and converts
   associations into versioned Parquet.
2. Stream complete phenotype and inheritance evidence tables into DuckDB/Parquet so memory stays
   bounded on genome-scale candidate sets.
3. Add established, container-pinned VEP/bcftools adapters and schema compatibility checks; do not
   infer missing annotations with an LLM.
4. Add genome-build, transcript, contig, ploidy, multi-allelic, and pedigree-consistency validation.
5. Calibrate ranking weights on additional public/synthetic truth sets with confidence intervals,
   sensitivity analysis, and regression thresholds.
6. Add an independent deterministic/LLM critic that reviews evidence completeness but cannot alter
   scores or branches directly.
7. Build a redacted Track 1 report schema with citations to data releases and explicit uncertainty.
8. Perform a privacy-reviewed, local-only authorized-data rehearsal with outbound networking and
   external tracing disabled, then document resource use and failure recovery.

Phase 4 should continue to defer literature RAG, external biomedical LLM calls, official submission
generation, Track 2, frontend work, and cloud deployment until separately authorized.

The complete copy/paste execution brief for a fresh coding-agent context is in
[`phase4_astra_handoff.md`](phase4_astra_handoff.md).
