# Proposed Phase 2 increment

After the Phase 1 substrate is accepted, the next increment should add:

1. a small provider-neutral `LLMBackend` protocol plus Ollama and mock implementations;
2. typed filter/count tools returning compact structured results;
3. a LangGraph variant-filtering state machine with strict iteration/tool/time limits;
4. conservative, phenotype-driven, pathogenicity, ClinVar, and novel-gene-rescue branches;
5. an append-only, redacted audit log recording every filter and candidate-count transition; and
6. synthetic agent-routing tests proving argument validation, critic execution, and termination.

No additional model family should be chosen until it is compared on tool selection, JSON schema
conformance, evidence interpretation, latency, and peak memory on the actual workflow.
