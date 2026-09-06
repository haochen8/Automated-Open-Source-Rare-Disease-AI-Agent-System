# Local Phase 4 operations

## Synthetic dry runs and resume

```bash
rare-disease-agent track1-dry-run --case missing-parent --output-dir /tmp/research-run --run-id research-001
rare-disease-agent benchmark-phase4 --output-dir /tmp/phase4-benchmark --seed 17
```

Run the identical dry-run command to resume. Do not reuse a run directory for a changed case,
resource, source checksum, seed, weights, or code version. A changed configuration is a new run.
A local file lock permits one writer. Stage directories are published only after their operation
finishes. The journal records start/end times, failures by exception type (never patient-bearing
exception text), software identity, dependency versions, host resources and stage hashes.

Committed preflight, pipeline and report stages are verified and reused. Work interrupted inside
an uncommitted stage is recomputed in that stage's staging directory. This is stage-level recovery,
not individual LangGraph-decision recovery. Committed decision audits remain byte-identical; the
stage-audit projection has immutable event IDs and can be reconstructed without duplicate events.
Modified or missing committed files are rejected rather than silently regenerated.

The private pipeline directory contains a self-contained membership/evidence/ranking DuckDB database,
Parquet evidence, complete streamed candidate/evidence JSON, metrics, ablations and local audits.
Only bounded summaries enter graph state: top phenotype genes, six model summaries, at most ten
compound pairs and small candidate samples. Returned workflow candidate/rank lists are capped at
100; complete results remain in artifacts. The redacted report includes at most 20 candidates.
Candidate pseudonyms omit coordinates, genotypes, individual IDs and raw variant/gene identifiers;
rank links the report back to the separately inspectable local database. Redaction is not permission
to publish a patient-derived report.

DuckDB membership/ranking uses a 1 GiB memory limit and two threads. Python evidence and rank batches
are normally 256 records. Compound pairing remains quadratic within a gene; a 100,000-pair work
budget fails closed and requires candidate refinement before re-inspection. This bounds work; it
is not a guarantee that every genome can be processed without an explicit filtering strategy.

## Explicitly authorized local data

No real dataset has been authorized or used during Phase 4 development. The programmatic
`workflows.authorized.authorized_dry_run` entry point supports a narrowly scoped rehearsal only after
an operator explicitly confirms the exact source and output paths. `LocalAuthorization` requires
`confirmed_local_research_use=True`, the input path and SHA-256, and the output directory.
Both patient input and derived output are rejected anywhere inside a Git checkout.

The input must already be normalized, annotated Parquet. Supply a typed `GenomicInput`, a verified
normalized HPO directory, the bounded HPO set, and a run ID. The context specifies genome/annotation
build, reference checksum, transcript release, biallelic normalization, sample identities, pedigree
and explicit X/Y PAR intervals. Preflight rejects incompatible builds, unsupported contigs,
duplicate variants/samples, inconsistent proband fields, invalid alleles, missing GQ, unsupported
ploidy and ambiguous sex chromosomes. Missing calls remain uncertain. Missing annotations require
an explicit research-mode allowance and are reported. Build/reference/normalization declarations
are operator attestations about prepared inputs; preflight cannot reconstruct a reference genome
from a Parquet file. Use the actual reference and verified annotation provenance when preparing it.

This entry point uses the deterministic mock planning backend, never downloads resources and never
opens external biomedical services. It emits a local research report without truth-case claims or
challenge-submission formats. It refuses an incomplete evidence plan or failed critic. Run-specific
paths, identifiers, normalized tables and audits remain private even though the report is redacted.

## Annotation previews

`tools.variants.adapters.ToolJob.preview()` constructs fixed bcftools normalization or VEP offline
annotation argument lists. It exposes no free-form shell/SQL or plugin arguments. Actual execution
requires explicit `authorized=True`, an installed matching tool, a matching reference checksum,
and a local version-matched VEP cache. It verifies the executable version and records its checksum.
The selected cache release is recorded; complete cache-file supply-chain verification is an
operational responsibility, not a claim that a version string proves every cache byte.

Execution is supervised for wall time, combined process-family RSS and output size; configured
threads are capped at four. The supervisor polls at 50 ms, so limits can be exceeded briefly before
termination. It kills the process group on failure, removes partial output, refuses existing output
and records input/output hashes, versions, exact command preview and timestamps. Output is not
captured into the agent conversation. No tool installation, cache download or actual annotation was
performed; every annotation-process test uses mocks. Tool behavior and output compatibility still
need an explicitly authorized installation-specific rehearsal.

Command references: [bcftools norm](https://www.htslib.org/doc/bcftools.html),
[VEP offline options](https://mart.ensembl.org/info/docs/tools/vep/script/vep_options.html).

## Verification

```bash
python -m pytest --cov=rare_disease_agent --cov-report=term-missing
python -m ruff check .
python -m ruff format --check .
python scripts/privacy_guard.py --all
python -m pip check
python -m hatchling build
git diff --check
```

All automated tests refuse outbound socket connections and use mock LLMs. External tracing enabled
through inherited LangSmith/LangChain environment settings causes the workflow to refuse execution.
The worktree development environment uses an existing-environment symlink; prefix local checks with
`PYTHONPATH=src` to ensure the current worktree is tested. Installed packages do not need that prefix.
