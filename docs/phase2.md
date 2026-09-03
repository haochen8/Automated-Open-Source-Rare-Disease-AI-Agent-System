# Phase 2: autonomous agent-controlled filtering

Phase 2 is implemented as a bounded LangGraph state machine driven through a provider-neutral LLM
protocol. CI and the reproducible demonstration use a deterministic scripted mock backend. A local,
loopback-only Ollama backend is optional and never downloads weights.

## Execution model

1. Convert the bundled synthetic VCF-like fixture to Parquet.
2. Load branch membership over deterministic DuckDB-derived rows.
3. Inspect aggregate statistics automatically.
4. Ask the variant planning agent for one `AgentDecision`.
5. Validate its action-specific Pydantic parameters.
6. Execute one typed deterministic tool into a new reversible branch.
7. Audit the decision, before/after counts, risk, model, and prompt hash.
8. Repeat until the agent stops or a hard safeguard terminates the graph.

The model has no arbitrary SQL or shell tool. Compact samples omit coordinates and genotypes. The
prompt explicitly prioritizes plausible-causal-variant preservation over candidate-count reduction.

## Branches and safeguards

The reference mock strategy creates conservative, pathogenicity, and phenotype-ready copies of the
full set. It applies a frequency filter with missing-frequency and ClinVar rescue, an independent
consequence filter, and a synthetic priority-gene filter. Their union becomes the final ensemble.

Exact and semantically equivalent repeated filters are detected with canonical signatures that
ignore target-branch renaming and list ordering. Empty or below-floor reductions are rejected and
their new branch is deleted. Source branches remain intact. Iteration, tool, invalid-decision,
tool-error, repetition, and no-reduction limits guarantee termination.

## Synthetic result

The bundled fixture contains 18 records covering common benign, rare synonymous, rare missense,
high-impact, missing-frequency novel-gene, ClinVar-rescue, and deliberately constructed causal cases.
The reference run produces a 10-candidate ensemble and preserves `SYNTH-CAUSAL-001`.

Artifacts are `audit_log.jsonl`, `metrics.json`, `final_candidates.json`, and the generated synthetic
Parquet file in a Git-ignored run directory. Metrics include initial/final counts, reduction ratio,
iterations, tool calls, stop reason, causal preservation, and process RSS at workflow start/end.

## Deliberate limits

Phase 2 is synthetic-only. It does not provide production VEP annotation, real HPO or inheritance
reasoning, ranking, external database integration, RAG, an official submission, Track 2, remote/cloud
inference, or multiple simultaneous models.
