# Phase 3 implementation

Phase 3 adds deterministic phenotype and inheritance evidence to the bounded Track 1 planner. It is
synthetic-only and does not access gated data, external biomedical models, literature, VEP, gnomAD,
or live ClinVar services.

## Evidence flow

```text
synthetic VCF + HPO identifiers + pedigree
                    ↓
bounded agent selects typed evidence operation
                    ↓
deterministic HPO / inheritance tool
                    ↓
reversible DuckDB candidate branches
                    ↓
deterministic preliminary ranking + ablations
```

The agent can inspect normalized HPO terms, phenotype-ranked genes, and aggregate inheritance
hypotheses. It cannot submit SQL, access raw genotype tables, invent associations, or calculate
ranking values. Pydantic validates every action and all evidence outputs.

## Synthetic benchmark

```bash
rare-disease-agent track1-synthetic --case de-novo
rare-disease-agent benchmark-synthetic --output-dir /tmp/phase3-benchmark
```

Cases cover de novo, homozygous recessive, confirmed-trans compound heterozygous, X-linked,
incomplete phenotype, and a causal gene absent from the association data. Each has a pathogenicity
decoy, a phenotype-fit/common-benign decoy, an unassociated rare damaging variant, low-quality
genotype evidence, and benign background candidates.

Run artifacts include:

- `candidate_membership.duckdb`: persistent branches and per-ablation ranking rows;
- `audit_log.jsonl`: append-only agent decisions, structured observations, and run provenance;
- `evidence.json`: complete synthetic phenotype and inheritance evidence;
- `ranked_candidates.json`: inspectable feature vectors and deterministic scores;
- `ablations.json`: four evidence combinations plus one-term phenotype removal;
- `track1_metrics.json`: causal rank/top-k, rescue survival, provenance, and RSS memory;
- `provenance.json`: Git, Python, dependencies, and HPO release identity.

The synthetic association fixture is not biomedical knowledge. For future public HPO use,
`import_public_hpo_file` only permits unauthenticated, allow-listed HTTPS sources, enforces a size
limit, supports pinned SHA-256 verification, and writes a provenance manifest. Production data must
pin explicit HPO release URLs rather than the moving `latest` alias.

## Scientific limits

Resnik BMA and the configured linear weights are transparent baselines for benchmarking architecture,
not validated diagnostic models. Absence of association is never a hard filter. Missing parent calls,
low genotype quality, and unconfirmed phase are explicitly uncertain. Real-data calibration,
population-specific inheritance logic, sex-chromosome ploidy edge cases, and production annotation
remain deferred.
