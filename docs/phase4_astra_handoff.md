# Phase 4 Astra execution brief

Copy everything below into a fresh Astra coding-agent context.

---

You are continuing the repository:

```text
/Users/Hao/Automated-Open-Source-Rare-Disease-AI-Agent-System
```

Start by inspecting `git status`, `git log -3 --oneline`, `README.md`, `docs/architecture.md`,
`docs/phase3.md`, and the Phase 3 tests. Do not rewrite working Phase 3 behavior without a demonstrated
need. Confirm the checkout is clean and that the Phase 3 completion commit is present before editing.

## Current verified baseline

Phases 0–3 provide:

- a provider-neutral LLM protocol with deterministic mock and optional loopback Ollama backends;
- M2/16 GB hardware-aware model recommendations and live-memory refusal checks;
- streaming synthetic VCF parsing into Parquet, including multi-sample `GT`/`GQ` preservation;
- a bounded LangGraph planner with Pydantic state, typed tools, safeguards, and prompt hashing;
- persistent DuckDB candidate membership and reversible conservative, phenotype, inheritance,
  pathogenicity, and novel-gene rescue branches;
- versioned HPO OBO/association loading, normalization, obsolete replacements, and Resnik BMA;
- deterministic dominant, recessive, homozygous recessive, de novo, X-linked, and compound-het
  evidence with explicit missing-data and phase uncertainty;
- deterministic quality/rarity/consequence/phenotype/inheritance ranking with four ablations;
- causal rank/top-k, phenotype-removal metrics, software/data provenance, and JSON/JSONL artifacts;
- six offline synthetic cases and mock-only CI coverage.

Re-run the existing gates before implementation and record the baseline:

```bash
.venv/bin/python -m pytest --cov=rare_disease_agent --cov-report=term-missing
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python scripts/privacy_guard.py --all
.venv/bin/python -m pip check
git diff --check
```

## Phase 4 objective

Harden Track 1 from a synthetic architecture proof into a reproducible, scalable, privacy-controlled
public-resource and authorized-data dry-run pipeline. Preserve the rule:

```text
agent selects evidence operation
        ↓
typed deterministic tool executes it
        ↓
versioned evidence is persisted
        ↓
deterministic ranker calculates scores
```

The LLM must never invent annotations, execute arbitrary SQL/shell, see entire genotype tables, or
perform ranking math.

## Required implementation

1. Public-resource lifecycle
   - Create a resource lock/manifest schema with source URL, immutable release, retrieval time,
     expected and observed SHA-256, size, license/citation, parser version, and output checksum.
   - Add `resources plan`, `resources fetch`, `resources verify`, and `resources status` commands.
   - Default to offline/status mode. Fetch only explicitly requested public allow-listed resources.
   - Reject moving `latest` URLs in reproducible configurations, credentials, query tokens,
     redirects to unapproved hosts, checksum mismatch, and oversized downloads.
   - Never commit downloaded archives or generated databases.

2. Production-shaped HPO ingestion
   - Parse a pinned official HPO OBO release and pinned gene/disease association files.
   - Normalize them into typed, versioned Parquet tables and indexed DuckDB views.
   - Preserve obsolete/replacement terms, evidence fields, disease identifiers, release provenance,
     and license references.
   - Support a tiny fixture with the same schema for offline tests; do not require network in CI.

3. Patient-scale evidence storage
   - Remove full phenotype-score dictionaries and inheritance-evidence lists from the runtime path.
   - Stream or batch complete evidence into DuckDB/Parquet tables keyed by `run_id`, `variant_id`,
     `gene`, method, and data version.
   - Keep only bounded top-k summaries in LangGraph state and agent observations.
   - Add indexes or query patterns and regression tests that demonstrate bounded memory on a large
     generated synthetic case. Do not generate millions of Python objects at once.

4. Deterministic variant validation and annotation adapters
   - Add typed preflight validation for genome build, contigs, sample identities, sex/ploidy,
     genotype fields, duplicate IDs, multi-allelic normalization status, transcript availability,
     and required annotations.
   - Define provider-neutral adapters for established bcftools/VEP execution with pinned tool/data
     versions, command previews, timeout/resource limits, and captured provenance.
   - Unit tests must use mock processes. Do not install tools, download caches, or run a production
     annotation job without explicit user authorization.

5. Ranking calibration and regression evaluation
   - Add more varied synthetic/public truth cases, including incomplete penetrance, affected sibling,
     missing parent, sex-chromosome edge cases, unphased compound het, mosaic/low-allele-fraction
     uncertainty, and annotation missingness.
   - Benchmark configurable weight sets and report rank, MRR, top-1/5/10 recall, branch recall,
     false-positive inheritance models, and uncertainty calibration.
   - Do not optimize weights on the same cases used as the final evaluation set. Persist the split,
     seed, configuration, and confidence intervals.

6. Evidence critic and report schema
   - Add a bounded critic step that checks evidence completeness, contradictions, provenance, rescue
     survival, and uncertainty language. It may request typed re-inspection but cannot modify scores,
     write SQL, or delete branches.
   - Create a redacted Track 1 research report schema containing ranked candidates, separately
     inspectable features, inheritance/phenotype evidence, limitations, release citations, and an
     explicit non-clinical-use warning.
   - Keep report generation local. No official challenge submission generation yet.

7. Recovery and reproducibility
   - Make runs restartable from persisted stage metadata without duplicating append-only audit events
     or corrupting candidate membership.
   - Record Git identity/dirty status, Python/dependencies, host resources, command configuration,
     resource lock hashes, tool versions, random seed, start/end timestamps, and failure reason.
   - Add integrity checks that detect mismatched run IDs, stale resources, partial artifacts, and
     incompatible schema versions.

## Hardware and privacy constraints

- Development host: MacBook Air 2022, Apple M2, 16 GB unified memory, macOS, no CUDA.
- Do not download or run frontier-scale models. Keep local inference to one Q4 7B–8B model when live
  inference is explicitly tested; use mock LLMs in all automated tests.
- Check live memory and disk before models, large imports, or generated stress cases.
- Keep deterministic bioinformatics and scoring separate from inference.
- Do not use real gated challenge data unless the user explicitly authorizes a narrowly scoped local
  dry run and confirms its path. Never commit patient data or derived patient artifacts.
- Keep external tracing and outbound patient-data transfer disabled.
- If the active five-hour Codex usage window exceeds roughly 90%, finish the current atomic check,
  report the checkpoint, and pause before beginning another large block. Do not consume a reset
  credit unless the user explicitly authorizes it.

## Required tests

Add unit tests for resource-lock validation, safe URL/redirect handling, checksums, HPO conversion,
bounded evidence batches, variant/pedigree/ploidy validation, adapter command construction, ranking
metrics, critic rules, resume integrity, provenance, and redaction.

Add integration/workflow tests for offline resource verification, normalized HPO-to-ranking flow,
large generated synthetic evidence storage, every new inheritance edge case, interrupted/resumed
runs, critic re-inspection limits, causal/rescue preservation, and report generation. CI must remain
network-free and mock-LLM-only.

## Success criteria

Phase 4 is complete only when:

- every old Phase 0–3 test still passes;
- public-resource fetch is explicit, checksum-pinned, licensed, reproducible, and optional;
- complete evidence is persistent and memory-bounded while graph state remains compact;
- validation fails closed on incompatible or ambiguous genomic inputs;
- annotation adapters are typed, previewable, bounded, and fully mocked in tests;
- benchmark train/evaluation splits and metrics are reproducible;
- the critic cannot bypass tool, score, branch, privacy, or iteration boundaries;
- resumable runs preserve audit and membership integrity;
- a redacted local research report is generated from synthetic data;
- Ruff, privacy, dependency, build, diff, and full test/coverage gates pass;
- all Phase 4 synthetic benchmarks complete within realistic M2/16 GB memory;
- no gated data, large model, annotation cache, or generated database is committed.

## Explicitly defer

Do not implement PubMed/literature RAG, external biomedical LLM calls, Track 2, frontend, cloud
deployment, live clinical use, or official challenge submission generation in Phase 4.

## Completion procedure

1. Run all tests with coverage, Ruff checks, privacy scan, `pip check`, package build, and
   `git diff --check`.
2. Run all synthetic Phase 4 benchmarks and report causal ranks, top-k/MRR, inheritance accuracy,
   rescue recall, memory, runtimes, and ablations.
3. Review `git status` and the complete diff for generated/private files.
4. Update README, architecture, privacy, resource, operational, and Phase 4 documentation.
5. Document unresolved scientific and operational limits honestly.
6. Commit exactly the completed Phase 4 changes with a clear message and push them. Do not commit or
   push before all gates pass.
7. Recommend Phase 5, but do not implement it automatically.

---
