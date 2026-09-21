"""Fixed-label resume diagnostics; input keys and values must never become message text."""

from rare_disease_agent.workflows.recovery import RunIdentityMismatch

# Paths are code-owned configuration fields, not filesystem paths or incoming dictionary keys.
IDENTITY_FIELDS = {
    ("inputs", "challenge_track1_vcf"): "original VCF fingerprint (checksum, size or timestamp)",
    ("inputs", "challenge_index"): "original index fingerprint (checksum, size or timestamp)",
    (
        "inputs",
        "challenge_phenotype",
    ): "phenotype document fingerprint (checksum, size or timestamp)",
    ("rehearsal_integrity_sha256",): "annotation rehearsal integrity manifest identity",
    ("hpo_manifest_sha256",): "HPO resource manifest identity",
    ("code_sha256",): "source-code identity",
    ("scope",): "execution scope",
    ("specification", "original_vcf"): "original VCF path identity",
    ("specification", "original_index"): "original index path identity",
    ("specification", "phenotype_docx"): "phenotype document path identity",
    ("specification", "annotation_rehearsal"): "annotation rehearsal path identity",
    ("specification", "hpo_directory"): "HPO resource path identity",
    ("specification", "output"): "output directory identity",
    ("specification", "confirmed_affected_sample"): "affected-sample confirmation",
    ("specification", "confirmed_local_research_use"): "local research-use confirmation",
    ("specification", "run_id"): "run ID",
    ("specification", "dataset_revision"): "dataset revision identity",
}
LABELS = {".".join(path): label for path, label in IDENTITY_FIELDS.items()}
LABELS.update(
    run_id="run ID",
    annotation_source="annotation rehearsal source VCF checksum",
    configuration="configuration/resource identity (unclassified mismatch)",
)
FRESH_RUN = (
    "A fresh run in a new output directory is required; "
    "use inputs with matching verified annotation provenance. "
    "Do not edit the existing journal or reuse its prepared stages."
)
WORKER_IDENTITY_MISMATCH = 2


class Phase5IdentityMismatch(RuntimeError):
    """Only allowlisted codes cross the private worker boundary."""

    def __init__(self, codes):
        # Treat even a worker receipt as untrusted. Never echo unknown codes or raw messages.
        if (
            not isinstance(codes, list)
            or not codes
            or len(codes) > len(LABELS)
            or any(not isinstance(code, str) or code not in LABELS for code in codes)
        ):
            codes = ["configuration"]
        self.codes = [code for code in LABELS if code in codes]
        labels = list(dict.fromkeys(LABELS[code] for code in self.codes))
        super().__init__(
            "Phase 5 input/provenance identity mismatch: " + "; ".join(labels) + ". " + FRESH_RUN
        )


def describe_mismatch(error: RunIdentityMismatch, current: dict) -> Phase5IdentityMismatch:
    missing = object()

    def value(configuration, path):
        for key in path:
            if not isinstance(configuration, dict):
                return missing
            configuration = configuration.get(key, missing)
        return configuration

    codes = [
        ".".join(path)
        for path in IDENTITY_FIELDS
        if value(error.previous_configuration, path) != value(current, path)
    ]
    if error.run_id_changed:
        codes.append("run_id")
    # The hash remains authoritative, including differences outside the known field inventory.
    return Phase5IdentityMismatch(codes)
