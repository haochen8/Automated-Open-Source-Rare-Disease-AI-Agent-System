# Model selection and hardware policy

Model selection is a benchmark question, not an architectural constant. The checked-in local
profile starts with one Q4 7B-class Ollama model shared across orchestrator, investigator, and
critic. This is a development convenience, not a scientific claim that the model is best.

On the baseline 16 GB M2 machine:

- prefer a 7B–8B Q4 model with an initially modest context (for example 8K tokens);
- avoid simultaneous large model instances;
- close memory-heavy applications if launch headroom is inadequate;
- run embeddings and large data operations separately where practical;
- use mocks in automated tests; and
- do not locally attempt full DeepSeek, GLM, or comparable frontier-scale models.

The catalog records approximate weight and runtime memory, not guarantees. KV cache, prompt length,
runtime implementation, batch size, and other active applications change actual consumption. Always
inspect live memory at launch time.

Production can assign separate larger families to each role on a compliant GPU server through the
same configurable backend interface. Raw patient data must not be sent to an external service until
the data rules and deployment have been explicitly reviewed.

Candidate tags and stored-size estimates were checked against the official Ollama pages for
[Qwen2.5](https://ollama.com/library/qwen2.5),
[Llama 3.1](https://ollama.com/library/llama3.1), and
[Mistral](https://ollama.com/library/mistral) in September 2026. Revalidate tags and licenses before
download. No model is downloaded automatically.
