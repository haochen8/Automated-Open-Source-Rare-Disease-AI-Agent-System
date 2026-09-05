# Privacy and public-repository policy

This public repository must not contain patient-level genomic or phenotype data, derived individual
tables, intermediate analysis artifacts, patient-bearing prompts or model outputs, access tokens,
credentials, or gated dataset copies.

Restricted storage is configured outside the checkout. Public-source caches and private run stores
remain separate. Final report generation must eventually perform rule-aware redaction and evidence
validation before any artifact leaves the controlled environment.

The pre-commit guard rejects known genomic and analytical file types even if `.gitignore` is
misconfigured. Synthetic VCF-like fixtures are permitted only in dedicated test or bundled-resource
paths with an explicit marker. The miniature Phase 3 HPO associations and pedigrees contain only
invented genes, diseases, and individuals. Treat a passing automated check as necessary, not
sufficient: review staged files before every public push.
