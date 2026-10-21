# Reproducing the final Track 1 export

[`scripts/track1_export.py`](../scripts/track1_export.py) reproduces the final CSV
formatting stage from an already sealed computational selection. It compares the
saved draft with its audit, then changes only chromosome field values according
to an explicit mapping. It preserves candidate order, both endpoints, positions,
reference and alternate strings, EPCR strings, finding types, and notes.

This command does **not** reproduce the preceding VCF processing, source binding,
annotation, inheritance computation, ranking, hypothesis selection, or EPCR
allocation. It cannot establish that earlier processing was scientifically
correct, that its inputs were complete, or that the organizer will accept the
submission. The [policy](../scripts/track1_submission_policy.py) is available
separately; invoking this exporter does not invoke that policy again.

## Required earlier artifacts

The source directory must already contain the four files emitted by the earlier
`saved-computational-submission-064-v1` adapter:

- `draft.csv`: the frozen selected hypotheses in submission order.
- `audit.json`: the saved selection, including endpoint source alleles, row ranks,
  EPCR strings, original evidence, and limitations.
- `manifest.json`: the earlier adapter version/status and draft/audit hashes.
- `integrity_manifest.json`: the hashes of those three files.

These are restricted derived artifacts. Supply their existing location outside
Git; do not copy them into this repository or publish them. The historical
adapter consumed a completed bounded analysis and its sealed saved assessment,
with their original source mappings, evidence, rankings, resource receipts, and
integrity chain. That adapter and its launcher dependencies were tied to local
paths and pinned historical code. This command neither generalizes nor reruns
that earlier chain. An independent end-to-end reproduction still requires those
upstream inputs and generation code; the final export alone is insufficient.

Obtain the expected SHA-256 of the existing `integrity_manifest.json` from a
trusted prior receipt or separately reviewed local record. Merely calculating a
hash of an untrusted directory does not establish its provenance. The exporter
checks the supplied digest, every sealed file digest, the manifest's draft/audit
digests, and agreement between saved hypotheses and draft endpoints/order/EPCR.
It does not revalidate the underlying scientific evidence or biological identity.

## Run locally

Copy [`configs/track1-export.json.example`](../configs/track1-export.json.example)
to restricted storage outside Git. Replace its placeholder paths and digest;
all supplied paths must be absolute and have no symlink or `..` components.
The source and output must be separate, outside Git, and the output must not
already exist. Its parent directory must already exist. Keep tracing disabled.

From the repository root, use the selected existing Python interpreter:

```sh
.venv/bin/python scripts/track1_export.py --config /absolute/restricted/path/export.json
```

The `.venv` name is an example, not an instruction to create an environment.
This command uses only Python's standard library and the adjacent local CSV
validator. It does not install dependencies, access the network, or upload files.
Current publication uses atomic no-replace directory rename on macOS or Linux
with `renameat2`; unsupported hosts/filesystems fail rather than overwrite output.

On success the new private directory contains:

- `submission.csv`: the corrected CSV, serialized with UTF-8 and LF line endings.
  Parsed nonchromosome field values are retained exactly, including notes and
  EPCR formatting; CSV quoting and line endings are deterministically serialized.
- `export_audit.json`: original source alleles, exported chromosomes, per-endpoint
  change flags, source/configuration/code hashes, and explicit stage limitations.
- `integrity_manifest.json`: checksums of the two new files.

The output directory uses mode `0700` and files use `0600`. Inputs are never
rewritten. Existing output is never replaced. Each input file is limited to
4 MiB and is reread before publication to reject changed contents. The draft
must also satisfy the validator's field/row contract. Success prints only a
fixed status; failure prints only `TRACK1_EXPORT_REFUSED` without paths, data,
or raw exception messages. Inspect restricted inputs locally when investigating
a refusal, without putting their contents in prompts or public logs.

## Explicit chromosome presentation

The mapping policy `grch38-primary-chr-prefix-v1` accepts exactly these forms:

| Supplied chromosome | Exported chromosome |
| --- | --- |
| `1` through `22` | `chr1` through `chr22` |
| `X`, `Y` | `chrX`, `chrY` |
| `chr1` through `chr22`, `chrX`, `chrY` | Unchanged |

No capitalization, zero-padding, mitochondrial alias, alternate-contig,
accession, liftover, reference/alternate normalization, or coordinate conversion
is guessed. Unsupported contigs are refused. The private mapping audit retains
the supplied source chromosome and all other allele fields. Endpoint reuse or
coordinate collisions after the mapping are refused. This mapping expresses the
published `chr` presentation convention for the supported primary chromosomes;
it does not prove reference assembly identity.

## Reproducibility and availability claims

The same sealed source files and mapping policy produce the same CSV bytes.
The export audit also binds the exact configuration bytes, so changing an output
path in the configuration changes that audit's hash even when the CSV is identical.
Keep the configuration, source seal, output seal, and reviewed exporter source
snapshot together in restricted storage for local reproduction.

If this exporter is still a local, uncommitted change, a link to an earlier
GitHub revision does not make it available there. State that limitation in the
completed methods report and retain a reviewable data-free source snapshot for
the local package. Do not describe the public revision as containing this command
until it actually does. Keeping a local package does not authorize publication
of its private configuration, audits, CSV, or report.

The [methods template](track1-methods-template.md) distinguishes configured
analysis declarations from independently verified biological facts. Prefix
formatting preserves the earlier notes; it does not establish sample identity,
affected status, phenotype linkage, phase, or causality. Correct any inaccurate
methods narrative separately without silently changing the selected hypotheses.
