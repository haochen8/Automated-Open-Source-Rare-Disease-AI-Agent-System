# Repository agent contract

## Purpose and architecture

This is a local-first rare-disease research pipeline, not a clinical diagnostic system.
Use the [README](README.md) for setup and interfaces and the
[architecture](docs/architecture.md) for component responsibilities.

- Python source lives in `src/rare_disease_agent`; tests use synthetic inputs.
- The pipeline uses VCF → Parquet → DuckDB for deterministic data processing.
- The model proposes schema-validated, typed tool operations within bounded control flow.
- Parsing, evidence computation, querying, ranking, and persistence remain deterministic.
- Keep model backends replaceable and scientific processing independent of model SDKs.
- Do not give model output arbitrary SQL or shell execution authority.
- Keep full variant tables and genotype evidence outside model context; use bounded summaries.

## Scientific and persistence invariants

Preserve these properties when changing the affected components; consult the
[architecture](docs/architecture.md) and
[representation contract](docs/phase5-execution.md#integrity-and-representation) for details.

- Preserve source alleles, genotype reconciliation, transcript consequences, and provenance.
- Do not invent annotations, clinical classifications, or missing scientific evidence.
- Missing evidence stays unknown; absence of an association is not exclusion evidence.
- Use supported population-frequency fields; caller allele frequency is not a substitute.
- Do not infer sample identity, affected status, sex, or parentage without supplied evidence.
- Preserve uncertainty in inheritance and phenotype linkage and retain inspectable evidence.
- Preserve conservative source membership and rescue behavior in the applicable workflow.
- Keep filtering reversible and preserve deterministic ordering and bounded processing.
- Keep evidence writes transactional and stage publication atomic.
- Preserve checksummed resume identity and immutable committed stages.
- Do not present a bounded rehearsal as whole-case coverage or a causal/clinical conclusion.
- Do not weaken tests, safeguards, or resource limits merely to obtain a passing result.

Scientific algorithms and numeric limits belong in their implementations and domain docs,
not in this instruction file. Intentional contract changes require task scope and validation.

## Privacy and authorization

The [privacy policy](docs/privacy.md) and [restricted-data setup](data/README.md)
apply to both inputs and derived artifacts.

- Keep patient data, private runs, credentials, and gated resources outside Git.
- Keep public resource caches separate from restricted inputs and private outputs.
- Do not inspect private artifacts merely to investigate an ordinary engineering task.
- Do not place patient content in prompts, logs, issues, fixtures, diffs, or commits.
- Patient-derived reports, metrics, and pseudonyms remain private; redaction is not
  authorization to publish or transfer them.
- Use deliberately synthetic fixtures and mock inference for ordinary development.
- Preserve tracing restrictions and fail-closed private-run preflight checks.
- A passing privacy scan does not prove privacy completeness; review content and scope.

Current task instructions govern authorization for private-data access, live inference,
model/resource downloads, external annotation execution, uploads, publication, submissions,
and Git commits, pushes, or merges. Do not infer permission from an available command,
a configured path, a historical run, or a phase document. If required authorization is
absent, ask before the affected action while continuing independent authorized work.
Do not add approval ceremonies for routine edits and checks already within task scope.

Ordinary verification is synthetic and adds no network operation or private-data run.
Do not silently install dependencies, fetch resources, or launch services to unblock it.
Annotation previews and execution are distinct operations; execution requires authorization.
Use [resource documentation](docs/resources.md) for explicit fetch/import provenance.
The test socket guard is not OS-level network isolation and does not cover all subprocesses.

## Before changing code

- Confirm the intended checkout, branch, HEAD, and working-tree status.
- Preserve pre-existing staged, unstaged, and untracked work; do not reset unrelated changes.
- Read the relevant source, tests, and linked documentation before choosing an approach.
- Select one existing Python interpreter explicitly and preserve virtual-environment selection.
- Do not silently fall back to another interpreter or assume an editable install is local.
- Verify that `rare_disease_agent` imports from this worktree; the canonical runner checks this.
- Surface missing prerequisites rather than installing or bypassing them without authorization.

A dirty tree is supported. It does not excuse unrelated changes or verification-time mutations.

## Proportionate engineering workflow

1. Investigate the task and its affected contracts; distinguish evidence from assumptions.
2. Form a plan appropriate to the change. Trivial edits need no separate planning ceremony.
3. Implement the smallest scoped change and meaningful regression coverage where needed.
4. Run focused checks, then the canonical gate when permitted and prerequisites allow.
5. Inspect the complete task diff, including staged, unstaged, deleted, and new files.
6. Correct defects and rerun relevant checks without weakening existing expectations.
7. Verify the final contents, review them, and check evidence status before reporting completion.

Separate environmental blockers from code regressions using observed failure evidence.
Do not classify a failure as environmental solely because similar tests passed previously.
Report unrun or blocked checks explicitly; focused successes do not replace the full gate.
Avoid unrelated refactors, generated-artifact commits, and changes made only to hide failures.
Do not automatically restore files changed during verification; investigate the mutation.

## Verification and completion evidence

Run from the repository root with the selected existing interpreter, for example:

```sh
make verify PYTHON=.venv/bin/python
make verification-status PYTHON=.venv/bin/python
```

The `.venv` path is an example, not an environment assumption. `PYTHON` must be one
executable name or path, not a command with flags. Use the same selection for both commands.
The [verification guide](docs/operations.md#verification), [Makefile](Makefile), and
[runner](scripts/verify.py) define checks, ordering, prerequisites, and failure behavior.
Do not substitute auto-fixing hooks, selective tests, or historical results for that gate.

The runner permits pre-existing task changes and normal generated artifacts, but rejects
relevant mutations during verification. Pause concurrent editing and run one verifier at a time.
Starting a new verification attempt invalidates an earlier PASS before checks begin.
See [completion evidence](docs/operations.md#completion-evidence) for the authoritative policy.

- **PASS:** all canonical checks completed successfully against matching repository contents.
- **FAIL:** a check or the final content-identity comparison failed.
- **INCOMPLETE:** prerequisites, inventory, interruption, or evidence handling prevented
  trustworthy completion; missing or invalid evidence is also incomplete.
- **STALE:** current contents, HEAD, or fingerprint policy differ from the completed record.

Only a current canonical PASS supports the statement **“fully verified.”**
`make verification-status` is a fast identity/status check; it does not rerun verification.
A relevant edit after verification invalidates prior completion evidence, including a
code, documentation, staged-only, or eligible untracked-file change.
Ignored generated outputs do not invalidate evidence; do not assume unknown files are ignored.
After a review-driven edit, obtain a new canonical result before claiming full verification.
If rerunning is blocked or outside task scope, report stale/incomplete/failed evidence honestly.

Content identity does not prove semantic correctness or adequate review. Review the final
complete diff separately, then check status after the last edit. Report the commands actually
run, results, current evidence status, and unresolved blockers. Do not combine supplementary
checks with an earlier failed run and describe the combination as a canonical PASS.
Evidence is local generated output, not product data or a receipt to commit.

## Deeper documentation

Read the relevant material rather than every historical phase document:

- [Operations](docs/operations.md): verification, synthetic runs, recovery, annotation previews.
- [Architecture](docs/architecture.md): component boundaries and deterministic evidence flow.
- [Privacy](docs/privacy.md): restricted artifacts and disclosure boundaries.
- [Resources](docs/resources.md): public provenance and explicit offline import/fetch workflows.
- [Execution](docs/phase5-execution.md): representation, integrity, and bounded-run limitations.
- [Scaling](docs/phase5-scaling.md): compatibility constraints and unresolved expansion gates.

Historical handoffs, validation counts, and phase plans are context, not current evidence or
current task authorization. Keep versions, detailed algorithms, check inventories, fingerprint
rules, and resource caps in their authoritative docs/code rather than duplicating them here.
