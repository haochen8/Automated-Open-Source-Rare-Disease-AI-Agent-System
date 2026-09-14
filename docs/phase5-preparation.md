# Phase 5 preparation plan

This document describes software behavior and public dependency proposals only. It contains no
patient-derived preflight results, paths, phenotype terms or candidate information.

## Prepared software and remaining integration

`ToolJob(annotation_profile="track1")` previews offline VEP with gene symbols, allele numbers,
canonical/MANE flags, gnomAD exome/genome frequencies, and a preferred consequence flag per allele
and gene. It retains other transcript consequences; it does not invoke a transcript-reducing pick
or population-frequency filter. Existing jobs default to the original minimal profile.

`VEPLayout` decodes the field order declared by a CSQ header, retains every consequence, validates
allele correspondence and refuses oversized/malformed payloads without patient-bearing errors.
Its population-frequency helper uses only explicitly named gnomAD fields. Missing values stay
unknown; neither raw VCF INFO/AF nor a generic CSQ AF field is a fallback. The maximum available
gnomAD frequency is used per allele, keeping population-source fields inspectable.

The contig planner maps standard aliases, rejects collisions and retains every unsupported contig
with a deferred disposition. It does not rename, filter or transform a VCF. A canonical-contig
rehearsal cannot be reported as complete genome coverage; original and deferred records must remain
recoverable and their counts reconciled before any full run.

The [bounded execution workflow](phase5-execution.md) now integrates these building blocks. The legacy
`parse_vcf` path assumes the project's synthetic INFO convention and must not ingest unadapted
callset AF or VEP CSQ. The new atomic importer retains every CSQ entry and creates one candidate
per source allele and gene, with a separate mapping to the consequence used for scoring. It verifies
source-allele, GT/GQ and annotation-stage reconciliation. Unconfirmed sample facts remain unknown.
Do not treat one representative transcript as the complete evidence for a variant.

Private input/output must remain outside Git. Ignore and privacy guards now additionally cover
`private_runs`, `private_cache` and `.vcf.gz.tbi`; this does not relax the external-path requirement.

## Proposed external dependencies requiring authorization

Use an isolated local environment; do not alter the project's existing Python environment. Proposed
tools are bcftools 1.22 and VEP 116 with a matching release-116 cache. Verify platform compatibility,
resolve exact package builds and hashes, and exercise synthetic inputs before touching real inputs.
Do not silently substitute another release if installation fails.

Public files proposed for a first standard-contig rehearsal:

| Resource | Compressed bytes reported by publisher HEAD |
|---|---:|
| Ensembl release 116 indexed human GRCh38 VEP cache | 27,644,657,162 |
| Ensembl release 116 GRCh38 primary-assembly FASTA | 881,964,081 |

Publisher endpoints, inspected for metadata only:

- [Indexed VEP cache](https://ftp.ensembl.org/pub/release-116/variation/indexed_vep_cache/homo_sapiens_vep_116_GRCh38.tar.gz)
- [Primary-assembly reference](https://ftp.ensembl.org/pub/release-116/fasta/homo_sapiens/dna/Homo_sapiens.GRCh38.dna.primary_assembly.fa.gz)

Content lengths are transfer estimates, not checksum verification or expanded-size guarantees.
Before fetching, verify publisher metadata/checksums where available and record release, URL,
size and integrity method. Record observed SHA-256 without falsely labeling it an independently
published SHA-256. Preserve download receipts locally. Do not bypass the existing HPO importer's
host/size/checksum restrictions to accommodate these different resource types.

The reference is provisional for a limited standard-contig rehearsal. Verify chromosome dictionary
compatibility and REF alleles against the intended inputs; primary-assembly coverage does not prove
coverage of alternative loci or decoys. A different/full reference requires a reviewed plan.

Proposed storage boundaries: at most 80 GiB for the isolated setup, archives, expansion and scratch,
and at least 25 GiB left free on the volume. Check both before and during downloads/extraction.
Stop if expanded resources exceed this budget. Prefer a single annotation worker and require live
RAM above the configured job memory allowance plus supervisor headroom before execution.

No gated SageBio files, raw reads, separate whole gnomAD downloads, model weights or patient-data
network requests are part of this proposal. A pinned HPO ontology/association bundle will also be
needed for phenotype scoring; its download requires separate resource authorization.

## Next execution sequence

The separately authorized HPO v2026-09-01 bundle was downloaded on 2026-09-10:
`hp.obo`, `genes_to_phenotype.txt`, and `phenotype.hpoa`, totaling 67,501,354 bytes.
All three matched their GitHub release publisher SHA-256 digests and sizes. Files and the original
download receipt are stored outside Git. Offline local import subsequently created independent,
checksum-verified cache copies with explicit local-import provenance. The fetch redirect policy is
unchanged. The normalized reference contains 20,482 ontology terms, 333,983 gene-association rows and
286,651 disease-association rows. Output checksums and indexed gene/disease lookups passed; normalized
outputs occupy 49,896,238 bytes. These are public-reference statistics, not patient results.

The release exposed OBO relationship qualifiers that the older loader included in identifier fields.
The loader now extracts relationship identifiers separately from trailing qualifiers/comments;
the original pinned ontology preserves the source annotations. A synthetic regression verifies
ancestry and replacement handling. The disease annotation header dates the annotations 2026-09-02
and references ontology 2026-09-01; both files belong to the pinned v2026-09-01 release asset bundle.

Public-resource setup completed on 2026-09-08: the downloaded VEP archive was verified and safely
extracted (27,673,136,989 expanded bytes). The browser-downloaded primary-assembly reference matched
the publisher checksum, passed gzip integrity, and was decompressed separately without changing
the original. Local receipts record compressed and uncompressed SHA-256 values. No patient input
was transformed. The cache and reference remain outside Git.

An initial installation dry run resolved Apple Silicon packages with 521,613,133 download bytes.
Installation was subsequently authorized. Native VEP startup failed in DB_File: the installed libdb
binary was Intel-only despite its arm64 package label; neither available arm64 libdb build fixed it.
The working installation uses a separate osx-64 environment under Rosetta, preserving bcftools 1.22
and VEP 116.0. Its exact package URLs are saved in a local explicit environment specification.

Installed-tool verification completed on 2026-09-10. bcftools 1.22 (HTSlib 1.24) split generated
multiallelic records and trimmed a generated allele correctly. VEP 116.0 annotated a generated
single-nucleotide substitution offline against the existing public reference/cache; its CSQ output
passed the strict decoder. No patient input was read by these checks. Their local receipts include
input/output, reference and executable checksums and job configurations. The unsuccessful native
environment is retained for diagnosis; use `phase5-x86` for the verified tools.

The process adapter now preserves the environment's executable launcher and helper search path,
uses VEP's supported `--help` version report, and tolerates a process exiting during an RSS query.
It continues to avoid inherited credentials and tracing settings. Synthetic regressions cover these
installation-discovered behaviors.

1. Obtain authorization for the public downloads and isolated tool installation above.
2. Verify exact dependencies and integrity, install locally within storage limits, and run synthetic
   tool rehearsals. No real annotation is implicitly authorized by installation permission.
3. Complete the atomic importer and private run manifest with synthetic-only regression tests.
4. Resolve sample/pedigree facts from explicit source metadata or user confirmation; do not invent
   sex, parent relationships or unaffected status. Missing segregation remains uncertain.
5. Review the concrete private rehearsal scope and command previews before previously prohibited
   production annotation. Preserve all originals and keep outputs outside Git.

Official behavior references:

- [VEP options](https://www.ensembl.org/info/docs/tools/vep/script/vep_options.html)
- [VEP cache contents and matching versions](https://mart.ensembl.org/info/docs/tools/vep/script/vep_cache.html)

No claim of completed Phase 5 validation, annotation or ranking is made by these preparation changes.

## Software verification

The offline suite passes 204 tests with 88% branch-aware coverage (67.87 seconds). The 19 variant
synthetic tests cover CSQ schema/allele validation, bounded decoding, retained transcripts,
population-frequency semantics, contig preservation/collisions, command previews and private-path
protection. Three additional resource regressions verify offline import integrity, CLI selection and
qualified OBO relationships. Ruff check/format, privacy guard, dependency consistency, wheel/sdist
build and diff checks also pass. Twelve additional regressions cover the atomic VEP importer,
complete synthetic dry run, changed inputs, stage resume, supervised execution, private-path checks,
explicit HPO extraction, unknown affected status and sample identity preservation. Installed tools
were additionally exercised on generated inputs
as described above; private execution authorization and results are recorded separately.

Pair evidence now commits as one bounded transaction, avoiding a durable commit for every pair.
Repeated keys within a batch retain the maximum score and last equal-score payload, matching
sequential upserts. Two additional integration tests verify this equivalence and rollback of both
pair rows and pair evidence after interruption. Pair joins also require the same chromosome.

The bounded run retains empty priority subbranches when the full conservative source remains
intact, records their zero counts, and includes that conservative source in the ensemble. A
synthetic case with no high-impact consequence verifies complete execution and resume. General
workflows retain their default candidate-floor behavior.

Unknown affected status caps every inheritance fit, including compound-heterozygous evidence.
A paired synthetic case verifies that this uncertainty rule cannot be bypassed by the pair path.

Expansion regressions cover deterministic autosomal sampling, complete record accounting,
reserved-tag and contig-alias rejection, explicit CLI authorization, reference/cache coverage,
read-only counting when index statistics are unavailable, larger genotype-ledger transactions,
and exclusion of unassigned genes and cross-chromosome pairs from compound evidence. VEP single
worker execution avoids forking, supports smaller internal buffers, and can record sampled resource
measurements. Private benchmark outcomes remain exclusively in external run receipts.

## Remaining Phase 5 completion work

Public-reference preparation and synthetic installed-tool verification are complete. Items 1–4
below are implemented for the bounded autosomal rehearsal by the new execution workflow. Items 5–6
are the private execution/verification gate, recorded in external run manifests. Full-input and
sex-chromosome/nonstandard coverage remain separate expansion work. Do not copy private results here.

1. Integrate streaming VEP ingestion into the private Parquet/DuckDB evidence store. Preserve every
   source row, split-allele mapping, genotype and transcript/gene consequence. Retain unsupported
   contigs with a deferred reason and reconcile counts; never imply complete coverage from a subset.
2. Integrate deterministic phenotype extraction/normalization and public HPO lookups. Preserve
   unknown and obsolete terms, provenance and unresolved sample/pedigree facts without inference.
3. Complete a reusable private execution wrapper: safe manifests, exact command previews, bounded
   resource checks, redacted failures, atomic stages and input-validated resume. Keep initial
   preflight read-only and require applicable authorization for additional patient processing.
4. Validate the complete integration with synthetic cases before defining the next concrete private
   run. Full annotation/ranking is not authorized merely by permission to install tools, prepare
   public references or execute a limited annotation rehearsal.
5. Complete an authorized deterministic Track 1 run with all candidate branches, explicit inheritance
   uncertainty, multiple ranking strategies and individual feature provenance.
6. Produce integrity-verified private rankings, research report, audit and aggregate metrics. Retain
   even aggregate patient results outside Git. Check resumability, runtime, peak memory, disk use,
   regression tests and privacy protection before declaring Phase 5 complete.

The optional local model experiment is not required to complete the deterministic baseline.
The [Phase 6 recommendation](phase6-recommendation.md) is a proposal only, pending these exit checks.
