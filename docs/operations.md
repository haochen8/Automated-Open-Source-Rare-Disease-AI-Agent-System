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

From this worktree's root, select the existing development interpreter explicitly:

```bash
make verify PYTHON=.venv/bin/python
```

`PYTHON` is one executable name or path, not a command with flags. Its default is `python3`.
The runner uses that interpreter for every Python check without resolving virtualenv executable
symlinks. It sets `PYTHONPATH` to this worktree's absolute `src` directory and verifies the imported
package's file and package directory before testing. This also applies to the shared `.venv`
symlink used by this worktree. Direct invocation with the chosen interpreter is supported:
`/path/to/python /path/to/worktree/scripts/verify.py` detects the root independently of the caller's
working directory. Run Make from the root or use `make -C /path/to/worktree verify PYTHON=...`.

The gate preserves the CI check order: full pytest with branch-aware coverage, Ruff lint, Ruff
format check, privacy scan, `pip check`, Hatchling wheel/sdist build, `git diff --check`, and
`git diff --cached --check`. It clears inherited `PYTEST_ADDOPTS` so external options cannot narrow
the suite. CI installs dependencies separately and invokes this same gate; verification itself
never installs, downloads, fixes, or formats anything. The separate privacy CI job remains intact.

Checks stream output and stop at the first failure. The script preserves the failing process's
exit code (signals use 128 plus the signal number); Make may return its own nonzero recipe-failure
code. Missing tools and unmet prerequisites fail rather than skip. Success requires every check
and the final mutation comparison to pass. Existing synthetic tests require their normal host
resources and process-inspection permissions, including the Phase 5 supervisor's 25 GiB free-disk
reserve. No test is omitted when these prerequisites are unavailable.

Pre-existing staged/unstaged changes are allowed. The runner fingerprints tracked file bytes, symlink
text, executable bits, index entries, and eligible untracked files before and after checks, including
after ordinary failures or interruption. Pause other editing during verification. Changes cause
failure and are never restored automatically. The comparison is an end-state check, not proof against transient
changes that were reverted; forceful termination can prevent the final comparison entirely.
An incomplete run is not successful verification.

Normal `.coverage`, `.pytest_cache`, `.ruff_cache`, `__pycache__`, `dist`, and pytest temporary
artifacts may be created; they are not automatically cleaned. Tracked standard output paths are
rejected before checks. Review the complete task diff separately: whitespace checks do not review
correctness, and content identity does not establish semantic correctness or review quality.

Tests use synthetic data and mock LLMs. The existing pytest socket guard does not provide OS-level
network isolation or automatically cover subprocesses. This gate adds no network operation or
private-data run and does not extend privacy-scanner semantics. Tracing-enabled application runs
remain subject to existing refusal checks. Dependency pinning and broader isolation are separate
work; a shared environment can still contain different dependency versions.

### Completion evidence

`make verify` atomically replaces `.verification/evidence.json` with INCOMPLETE before inventory,
imports, or checks, so an interrupted new attempt cannot reuse an earlier PASS. It records schema
and fingerprint-policy versions, branch/HEAD, starting/ending fingerprints, selected interpreter and
Python version, verified relative import identity, ordered commands/results, timestamps and a
sanitized failure category. It does not store source contents, raw logs or environment dumps.
Evidence-publication failure returns nonzero. The evidence directory must be untracked and cannot
be a symlink; the record cannot be a symlink either. Receipts are local generated files, not product
artifacts or attestations protected against deliberate tampering.

```bash
make verification-status PYTHON=.venv/bin/python
```

This fast command hashes local engineering files without running checks. Only current PASS returns
zero. FAIL means a check or final content-identity comparison failed. INCOMPLETE means a prerequisite,
inventory, receipt, interruption or publication problem prevented trustworthy completion. STALE is
derived when current fingerprint, HEAD or fingerprint policy differs from a completed record; the
recorded execution outcome is retained. Missing, malformed and incompatible evidence is INCOMPLETE.
A branch-name change alone does not invalidate identical contents at the same HEAD.

The SHA-256 manifest uses sorted relative paths, content hashes, executable flags, symlink text,
missing-file markers and index object identities/modes/stages. Absolute paths, mtimes, ignored
outputs and the reserved evidence directory are excluded. Symlink targets are never opened and
symlink ancestors are refused. Known private/generated paths and prohibited formats are rejected
before inventory files are read. This is not a guarantee against private text embedded in otherwise
legitimate source or documentation; the existing privacy scan remains necessary and unchanged.

Eligible untracked files are Python under `src`, `scripts`, `tests`; Markdown at root or under `docs`;
YAML/example configuration under `configs`; YAML under `.github/workflows`; and the named root
build/configuration files listed in `ROOT_FILES` in the verifier. Every other unignored untracked
file makes completion INCOMPLETE without content inspection. New fixture formats require an explicit
policy decision rather than silently being omitted. Tracked files remain included unless prohibited.

Documentation edits, new/deleted source files, symlink or executable changes, and staged-only changes
invalidate evidence. Coverage/cache/build updates do not. Review the complete diff including new
files, then check status; any review-driven edit requires another canonical run. A matching receipt
proves content identity only, not review quality, environment reproducibility or task correctness.
Run one verifier at a time and pause concurrent edits. End-state hashes cannot detect a transient
edit that was restored or prevent an edit immediately after the status command. Dependency locks and OS isolation remain out of scope.
