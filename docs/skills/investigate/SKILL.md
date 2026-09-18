---
name: investigate
description: Investigate unexplained behavior, regressions, feature constraints, or unfamiliar code paths using focused evidence before implementation.
---

# Investigate

Produce a supported explanation and a justified next action before changing code.
Apply the current task instructions and [repository contract](../../../AGENTS.md).
This skill adds investigation technique, not new permissions or completion rules.

## When to use

Use for an unexplained failure, regression, unfamiliar execution/data path, architectural
question, or feature request whose existing behavior and constraints need clarification.
Use it when competing explanations would lead to different changes.

Skip a formal investigation for an obvious localized edit with understood behavior.
This skill does not replace implementation, review, or canonical verification.

## Invocation and scope

A natural-language question or symptom is enough; no special command syntax is required.
Optional context includes expected behavior, a synthetic reproducer, suspected subsystem,
constraints, and an investigation budget. Do not require a form before starting.
Investigation alone does not authorize implementation or source/test edits.
If the enclosing task authorizes implementation, first state the investigation conclusion,
then transition explicitly to that work within the authorized scope.

## Workflow

Scale the depth to the question; combine steps when the answer is straightforward.
Do not impose quotas for files, hypotheses, commands, or turns.

1. Establish the question: observed versus expected behavior, or the decision to support.
   Separate the reported symptom from the user's suggested cause. Ask for clarification
   only when missing intent materially changes the investigation.
2. Locate the likely entry point, subsystem boundary, inputs, outputs, and dependencies.
   Search specific symbols or error text before reading broader modules.
3. Inspect current behavior and immediate callers. Trace the relevant path from input
   through validation/configuration and transformation to persistence or output.
   Stop tracing once the behavior is explained; expand only for a concrete dependency.
4. Inspect relevant tests, normal behavior, failure cases, and nearby boundaries.
   Distinguish assertions found in tests from results actually observed in this task.
5. Identify the applicable repository invariants through AGENTS.md and relevant domain
   documentation. Read only the material needed to constrain this solution.
6. When the cause is uncertain, form plausible competing hypotheses and identify an
   observation that would distinguish them. Do not assume the first plausible cause.
   Do not invent alternatives when direct evidence already settles the question.
7. Obtain the cheapest permitted deterministic evidence that can resolve uncertainty:
   source/configuration inspection, an existing focused synthetic test, or a minimal
   temporary synthetic probe. State what each check is intended to discriminate.
8. Classify the finding and recommend the smallest justified next action, including
   no code change. For feature/architecture questions, compare behavior and contract
   options rather than forcing a root-cause explanation.

## Evidence discipline

Use these distinctions wherever uncertainty affects the conclusion:

- **Verified fact:** directly supported by inspected source or observed execution;
  identify which kind and cite the supporting path/symbol or command/result.
- **Hypothesis:** plausible explanation with supporting evidence and a remaining
  discriminator; include contrary evidence rather than hiding it.
- **Unknown:** information unavailable or not established by permitted checks.

Source inspection establishes code structure, not that a runtime branch executed.
Historical reports may guide searches but are not current execution evidence.
A focused test supports its exercised case, not the entire subsystem.
Record commands actually run, exit codes, concise observations, and relevant interpreter
or synthetic setup needed to reproduce them; do not replace evidence with confidence.

Do not edit before understanding the relevant path or change tests to support a preferred
explanation. Temporary synthetic probes must leave repository sources/tests unchanged.
Label mocked conditions; they do not establish the state of the real environment.
Prefer synthetic/local evidence when sufficient; do not reach for live/private resources.
Avoid broad unrelated exploration, and never force certainty from incomplete evidence.

## Stop or escalate

Stop successfully when the answer and recommended action are supported and further
exploration would not change that recommendation.
Return a bounded unresolved result when a necessary observation is blocked by missing
prerequisites or authorization, intent is ambiguous, an agreed budget is exhausted,
or repeated exploration adds no evidence. Continue independent permitted work if useful.
Name the exact missing decision/evidence and the next discriminating check; ask a targeted
question when human input is needed rather than requesting vague further direction.

## Investigation output

Use concise Markdown; combine fields for small questions, and omit inapplicable detail.

- **Question:** observed/expected behavior or decision, with the relevant scope.
- **Relevant path:** entry point and a short execution/data trace with source references.
- **Findings:** verified facts; hypotheses tested, contrary evidence, and unknowns as needed.
- **Classification:** implementation defect, environment/configuration issue,
  specification ambiguity, missing verification/test coverage, expected behavior,
  or unresolved with a precise next discriminator. Multiple findings may coexist.
- **Recommendation:** what should change, if anything; likely locations and constraints.
- **Evidence and limits:** actual commands/results, reproduction context, unrun checks,
  blockers, and remaining uncertainty. Use repository-relative paths and symbols;
  add line references when useful. Distinguish inspected tests from executed tests.
- **Change status:** whether anything changed; investigation normally makes no source edits.

The handoff should let another agent continue without inheriting an unsupported diagnosis.
An investigation conclusion is not canonical PASS evidence.
