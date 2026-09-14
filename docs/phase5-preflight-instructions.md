# Phase 5 authorized local read-only preflight

## Subsequent standing authorization

The user later instructed the agent to complete Phase 5 and continue. This authorizes the local
deterministic end-to-end dry run, including private phenotype/inheritance scoring, rankings and
reports. The original preflight-only stop is historical. The implemented first run uses the
previously annotated bounded subset; it does not establish whole-genome coverage. Unconfirmed
sample context remains unknown rather than being asserted as fact. Additional downloads and
uploads remain outside this authorization. See [execution details](phase5-execution.md).

The user subsequently instructed the agent to continue with analyzed next actions automatically,
asking only for missing information or permissions such as additional downloads. The initial
preflight stop below describes the earlier gate; it no longer prevents routine preparation work
after reporting that preflight. Downloads, tool installation and previously prohibited production
annotation still require the applicable authorization. Gated downloads and patient-data uploads
remain prohibited. See [the preparation plan](phase5-preparation.md) for the next bounded stage.

## Original preflight scope

This revision replaces the supplied broad Phase 5 execution brief for the current task.
The current authorization covers inspection of the three user-designated local inputs only.
The full real-data dry run remains a future phase requiring a separate instruction to proceed.
This file contains instructions only; do not add patient-derived findings to it.

## Baseline

Start from the verified Phase 4 commit `6538501`. Inspect Git status and preserve existing work.
Phase 4 passed 150 offline tests with 88% coverage. Its synthetic evaluation does not establish
performance on real cases. Do not alter historical validation records based on patient data.

## Authorized inputs and storage

Use only the existing local clinical phenotype DOCX, designated compressed VCF, and its TBI.
Do not download gated SageBio files, authenticate to challenge services, or implement a fetch
command for this task. Do not inspect the contents of other files. A browser `.crdownload`
file is an incomplete download, not a substitute for the designated completed VCF.

Keep the original files in place during read-only preflight. Their existing external directory
is outside Git. A future `private_data/sagebio/` arrangement must preserve that protection:
the Phase 4 authorized-run interface rejects input and output paths inside any Git checkout,
even if ignored. Prefer an external private directory. Do not weaken this check just to use
the suggested relative folder name, or move/copy files during this preflight.

The original brief's `private_runs/` and `private_cache/` names were not covered by the Phase 4
directory ignore rules. Phase 5 preparation adds ignore and privacy-guard coverage for both.
Do not assume folder naming alone protects contents.
Keep any future patient-derived outputs outside Git and verify protection before writing them.
Do not commit, stage, push, publish, or use patient files or derived artifacts as test fixtures.

## Read-only inspection

Inspect file presence, readability and byte sizes. Use local deterministic code and installed
read-only tools; do not install tools, download resources, call an LLM, enable external tracing,
or send patient contents to external services. Suppress raw records, header sample names,
clinical prose, HPO identifiers and patient-bearing exception messages in tool output.

For the VCF, report only sanitized metadata:

- Compression integrity and BGZF compatibility, VCF schema and completeness.
- Genome build from explicit metadata or corroborating reference dictionary evidence; otherwise
  report unknown. Contig naming alone is insufficient to assign a build.
- Contig naming convention, sample count and roles only when explicitly supported.
- FORMAT and INFO field names, genotype availability and existing annotation categories.
- Actual record count from a bounded streaming scan, or a clearly identified estimate if an
  exact scan is not performed. Do not infer record counts from index chunk counts.
- Multiallelic representation and evidence relevant to normalization. Do not claim left alignment
  is verified without the matching reference sequence.
- Missing ranking annotations and whether normalization or annotation is needed, without
  invoking bcftools normalization or VEP.

For the TBI, validate decompression, structure and coordinate-index configuration. When the VCF
is present, check that index references/offsets correspond to it and exercise bounded indexed
region reads against a sequential read. Distinguish internal index validity from verified pairing;
the TBI alone cannot establish that it matches the VCF. Never rewrite or regenerate the index.

For the DOCX, inspect ZIP/XML integrity and document structure locally. Determine whether explicit
HPO IDs are present and whether the document contains tables, narrative text or both. Report only
presence/counts and structural categories. Do not display clinical prose, HPO IDs, document images
or extracted patient text. HPO normalization and clinical interpretation are outside this step.

If an input is missing or incomplete, finish only independent checks on the authorized files that
are present. Mark dependent checks unverified; do not rename partial downloads, fabricate metadata,
repair files or proceed to ingestion.

## Resources and reporting

Check available RAM and disk. Stream reads with bounded buffers and avoid materializing the VCF
or creating decompressed copies. Distinguish current resource measurements from planning estimates.
Do not estimate annotation cache requirements until the genome build, annotation gaps and intended
tool/cache release are known. Record no patient-derived results in public repository documentation.

Stop after reporting to the user:

1. Exact designated local files detected or missing.
2. File sizes.
3. VCF genome build, confidence and unresolved evidence.
4. Sample count, supported roles and available genotype fields.
5. Existing annotation fields.
6. Whether explicit HPO IDs are present and whether phenotype is structured or narrative.
7. Whether normalization is needed or cannot yet be determined.
8. Whether additional annotation is needed or cannot yet be determined.
9. Estimated disk/RAM needs for the next step, alongside measured availability.
10. Recommended next Phase 5 action and any blocking condition.

Explicitly state whether VCF/index pairing and indexed access were verified. Do not imply that
an incomplete preflight passed. Do not commit or push changes as part of this preflight task.

## Deferred work

Do not automatically proceed to full-VCF transformation, Parquet/DuckDB ingestion, phenotype
normalization, annotation, inheritance analysis, ranking, candidate strategies or research reports.
Do not generate patient-derived public artifacts, even if aggregate or labeled redacted.
There is no authorization for an LLM experiment, official submission or raw-sequencing processing.

After the user separately authorizes the next stage, use the completed preflight to define a narrow
local rehearsal with exact input/output scope, verified resource/tool versions, conservative
filtering, deterministic scoring, private provenance, resource limits and synthetic-only tests.
Successful preflight alone does not authorize that rehearsal or establish Phase 5 completion.
