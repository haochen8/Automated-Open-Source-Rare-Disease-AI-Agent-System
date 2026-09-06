# Privacy and public-repository policy

This public repository must not contain patient-level genomic or phenotype data, derived individual
tables, intermediate analysis artifacts, patient-bearing prompts or model outputs, access tokens,
credentials, or gated dataset copies.

Restricted storage is configured outside the checkout. Public-source caches and private run stores
remain separate. Phase 4 report generation performs field-allowlist redaction and bounded evidence review locally.
No report is automatically transferred or submitted.

The pre-commit guard rejects known genomic and analytical file types even if `.gitignore` is
misconfigured. Synthetic VCF-like fixtures are permitted only in dedicated test or bundled-resource
paths with an explicit marker. The miniature Phase 3 HPO associations and pedigrees contain only
invented genes, diseases, and individuals. Treat a passing automated check as necessary, not
sufficient: review staged files before every public push.


Phase 4 explicit authorization is scoped to an exact local input checksum and output directory.
The authorized-data entry point rejects source/derived paths inside any Git checkout and uses only
the mock planner. External tracing flags cause refusal. Annotation adapters default to preview and
require a separate explicit execution authorization; no tool/cache installation is automated.

Public fetch accepts only pinned, credential-free official HPO source paths. Any query token or
unapproved redirect fails before the next request. Public lock/receipt provenance is separate from
private case provenance. Generated resources, analytical databases, archives and staging artifacts
are ignored; the final commit review must still inspect every staged file.

Research report pseudonyms, feature counts and model-fit summaries do not make derived patient
data public. Full genotype evidence is intentionally retained in private DuckDB/Parquet for local
inspection; neither it nor the patient-specific audit belongs in issues, prompts or commits.
Failure journals record exception classes, not raw exception messages that may include patient IDs.
