# Engineering subagent ledger

This ledger records **50 delegated engineering workers** found in the available local
worker metadata for this project checkout, including the worker preparing this document.
The inventory was collected on **2026-10-21**. It describes the available records, not an
exhaustive history of every agent or chat ever used for the project.

The source was an allow-listed metadata extract containing task identifiers, display
codenames, model settings, and creation timestamps. All 50 records specify
`gpt-6-astra` with `ultra` reasoning effort. Local UUIDs, raw prompts, session logs,
private filesystem paths, patient information, candidate content, and private execution
results are omitted. No private run artifacts were inspected to prepare this ledger.

## What the records establish

Task identifiers and codenames below are recorded metadata. The **scope summaries are
inferred from task labels**; they do not establish code ownership, implementation
contributions, completion, review approval, verification results, or scientific outcomes.
For example, a task named `review_failures` establishes that a worker had that task
identifier, not that it resolved a failure or passed a review. Codenames can repeat and
are not unique identifiers.

The current, directly observed contribution of `/root/subagent_ledger` is preparation
of this file and comparison of its worker identifiers with the supplied inventory.
Historical contributions have not been independently reconstructed from session logs.
Canonical verification remains governed by
[completion evidence](../operations.md#completion-evidence).

## Recorded workers

Rows follow metadata creation order. `/root/...` entries are task identifiers, not
filesystem paths. Nested identifiers preserve parent-child delegation, including the
five recorded descendants. The count covers worker records, not simultaneous workers.

| # | Task identifier | Codename | Inferred technical scope |
| --- | --- | --- | --- |
| 1 | `/root/review_failures` | Galileo | Failure review. |
| 2 | `/root/review_coverage` | Wegener | Coverage review. |
| 3 | `/root/review_identity` | Rawls | Identity consistency review. |
| 4 | `/root/review_privacy_tests` | Wegener | Privacy test review. |
| 5 | `/root/receipt_isolation_review` | Godel | Receipt isolation review. |
| 6 | `/root/regression_coverage_review` | Wegener | Regression coverage review. |
| 7 | `/root/phase5_data_gaps` | Lagrange | Phase 5 data gap analysis. |
| 8 | `/root/phase5_storage_gaps` | Sagan | Phase 5 storage gap analysis. |
| 9 | `/root/pair_semantics` | Plato | Pairing semantics. |
| 10 | `/root/pair_performance` | Erdos | Pairing performance. |
| 11 | `/root/coverage_contracts` | Popper | Coverage contracts. |
| 12 | `/root/historical_gap_check` | Boole | Historical gap check. |
| 13 | `/root/benchmark_atomic_publication` | Rawls | Atomic publication of benchmark artifacts. |
| 14 | `/root/label_free_artifact_gap` | Poincare | Artifact gaps in label-free processing. |
| 15 | `/root/sensitivity_api_review` | Fermat | Sensitivity API review. |
| 16 | `/root/sensitivity_design_review` | Kierkegaard | Sensitivity design review. |
| 17 | `/root/sensitivity_api_review/rarity_synthetic_tests` | Lagrange | Synthetic rarity tests within the sensitivity API task. |
| 18 | `/root/rarity_supervisor_review` | Ampere | Rarity supervision review. |
| 19 | `/root/phenotype_protocol_bounds` | Kepler | Phenotype protocol bounds. |
| 20 | `/root/phenotype_wrapper_tests` | Hume | Phenotype wrapper tests. |
| 21 | `/root/phenotype018_wrapper_review` | Feynman | Review of the wrapper labeled phenotype018. |
| 22 | `/root/action019_claim_review` | Poincare | Review of claims associated with action019. |
| 23 | `/root/action019_wrapper_review` | Wegener | Review of the wrapper labeled action019. |
| 24 | `/root/near_cap_review` | Aristotle | Behavior near resource caps. |
| 25 | `/root/capacity_inventory_core` | McClintock | Core capacity inventory. |
| 26 | `/root/capacity_inventory_wrapper_tests` | Hilbert | Capacity inventory wrapper tests. |
| 27 | `/root/storage_probe_review` | Kuhn | Storage probe review. |
| 28 | `/root/storage_probe_impl` | Bernoulli | Storage probe implementation. |
| 29 | `/root/coverage_next_review` | Turing | Review of coverage next steps. |
| 30 | `/root/capacity_policy_review` | Sagan | Capacity policy review. |
| 31 | `/root/organizer_provenance` | Herschel | Provenance of organizer material. |
| 32 | `/root/capacity_policy_review/checker_review` | Parfit | Checker review within the capacity policy task. |
| 33 | `/root/postreplay_priorities` | Archimedes | Priorities after replay. |
| 34 | `/root/postreplay_priorities/helper_review` | Einstein | Helper review within the post-replay priorities task. |
| 35 | `/root/whole_case_scaling_review` | Cicero | Whole-case scaling review. |
| 36 | `/root/whole_case_scaling_review/exact_global_pair_review` | Ptolemy | Exact global pairing review within the scaling task. |
| 37 | `/root/equivalence_performance` | Volta | Equivalence and performance. |
| 38 | `/root/recover_reviewed_drafts` | Boyle | Recovery of reviewed drafts. |
| 39 | `/root/equivalence_performance/review_stream_design` | Wegener | Streaming design review within the equivalence and performance task. |
| 40 | `/root/submission_route_review` | Singer | Submission route review. |
| 41 | `/root/submission_requirements` | Laplace | Submission requirements. |
| 42 | `/root/submission_policy_core` | Descartes | Core submission policy. |
| 43 | `/root/submission_saved_adapter` | Plato | Submission adapter for saved artifacts. |
| 44 | `/root/upload_methods_review` | Socrates | Upload methods review. |
| 45 | `/root/upload_requirements_review` | James | Upload requirements review. |
| 46 | `/root/submission_export_fix` | Gibbs | Submission export correction. |
| 47 | `/root/submission_methods_review` | Erdos | Submission methods review. |
| 48 | `/root/track1_readme` | Nash | Track 1 README documentation. |
| 49 | `/root/publication_review` | Ohm | Publication review. |
| 50 | `/root/subagent_ledger` | Averroes | Engineering subagent inventory documentation. |

## Other uses of “agent”

**User-owned collaboration chats** are separate conversations. They are not spawned
engineering subagents merely because they concern the same repository. This metadata
inventory does not enumerate those chats, and none are added to the count above.

**Runtime pipeline components** are software in this repository. They are separate from
the engineering workers that help develop it:

| Component | Implemented responsibility | Public reference |
| --- | --- | --- |
| `VariantFilteringAgent` | Proposes one schema-validated `AgentDecision` from bounded workflow summaries through a replaceable backend. | [Planner](../../src/rare_disease_agent/agents/variant_agent.py), [typed schemas](../../src/rare_disease_agent/agents/schemas.py) |
| Mock planner backend | Supplies deterministic scripted responses for automated agent tests. | [`MockLLMBackend`](../../src/rare_disease_agent/llm/mock.py) |
| Evidence critic | Performs deterministic checks with bounded, typed, read-only re-inspection; it cannot change scores, branches, or planner budgets. | [Critic](../../src/rare_disease_agent/reporting/research.py), [architecture](../architecture.md#phase-4-persistent-runtime-and-recovery) |

The ledger does not imply 50 runtime agents or model instances. The
[architecture](../architecture.md#separation-of-concerns) separates planning from
deterministic parsing, evidence, ranking, and persistence. These public component links
describe the software; they do not assign ownership to any worker or establish private
execution progress.

**The automatic guardian / approval reviewer** is platform infrastructure that reviews
tool-action authorization. It is excluded from the engineering-worker count and is
separate from the repository's runtime evidence critic. Its approval decisions are not
code review or canonical verification evidence.

## Maintaining this ledger

Add workers only from available delegated-worker metadata, preserving nested task
identifiers. Keep metadata facts separate from inferred scope and directly verified
contributions. Do not copy raw prompts, session logs, patient-derived artifacts, or
private execution details into this public document. Follow the repository
[privacy policy](../privacy.md) when preparing any update.
