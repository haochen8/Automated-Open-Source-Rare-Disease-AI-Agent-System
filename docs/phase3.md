# Recommended Phase 3 plan

Phase 3 should add phenotype and inheritance evidence without weakening the Phase 2 boundaries:

1. Normalize synthetic HPO terms and query versioned local/public HPO association data.
2. Produce deterministic gene–phenotype similarity features with provenance.
3. Add typed pedigree/genotype tools for dominant, recessive, X-linked, de novo, homozygous, and
   compound-heterozygous hypotheses.
4. Feed only compact phenotype and inheritance summaries into the existing planning state.
5. Replace the Phase 2 synthetic priority list with tool-derived evidence while retaining the
   conservative and novel-gene rescue branches.
6. Extend metrics with causal rank, phenotype ablations, inheritance-fit accuracy, and branch recall.
7. Persist patient-scale branch membership in DuckDB/Parquet outside Git rather than Python sets.
8. Add database/tool version provenance and software commit identity to every audit run.

Phase 3 should remain synthetic-first and use the mock LLM in CI. Real gated data and production VEP
annotation should remain deferred until their privacy and operational workflows are explicitly ready.
