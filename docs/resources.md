# Reproducible public resources

Phase 4 performs no automatic downloads. `resources` without a subcommand reports offline mode.
Only the explicit `fetch` operation opens a network connection, for one named resource.

```bash
rare-disease-agent resources
rare-disease-agent resources plan /private/path/hpo-locks.json
rare-disease-agent resources status /private/path/hpo-locks.json --cache /private/public-cache
rare-disease-agent resources fetch /private/path/hpo-locks.json --name ontology --cache /private/public-cache
rare-disease-agent resources verify /private/path/hpo-locks.json --cache /private/public-cache
rare-disease-agent resources normalize-hpo /private/path/hpo-locks.json /private/normalized-hpo --cache /private/public-cache
```

A lock file is a JSON array of `ResourceLock` records. Each record requires `name`, `kind`
(`obo`, `genes`, or `diseases`), `source_url`, `release`, `expected_sha256`, `expected_size`,
`license_reference`, and `citation`. Schema version is 1; parser version is `hpo-normalizer-v1`.
The release must be a dated tag or full 40-character commit. The release component must appear in
an official `obophenotype/human-phenotype-ontology` URL on GitHub or raw.githubusercontent.com.
Use independently verified checksums and sizes; there are no unchecked public defaults.

The receipt records the complete lock, lock hash, observed checksum/size, and retrieval timestamp.
Normalized output manifests include those receipts with each normalized table's output checksum,
plus checksums for all Parquet files and the DuckDB database. Verification is offline. Any changed
input, incomplete manifest, unsupported schema, or modified output fails validation. Failed
conversion staging is removed; a complete output directory is published atomically.

HTTPS is mandatory. Credentials, any query string (including signed download tokens), fragments,
nonstandard ports, moving `latest`/`main`/`master` paths, path traversal and unapproved redirect hosts
are rejected. Redirects are inspected before following them, with at most six requests. Sources
are bounded to their exact locked size and a 512 MiB ceiling. Downloads use a 30-second HTTP
operation timeout and do not inherit proxy credentials. A GitHub asset that redirects to a signed
query URL is deliberately refused; use an immutable raw source where available. This restriction
can make some official release assets unavailable through this importer. Do not weaken it or
strip a signed URL's query to pretend a download was verified.

The official association schemas are described by the HPO project:

- [Gene associations](https://obophenotype.github.io/human-phenotype-ontology/annotations/genes_to_phenotype/)
- [Disease annotations](https://obophenotype.github.io/human-phenotype-ontology/annotations/introduction/)
- [Pinned release assets](https://github.com/obophenotype/human-phenotype-ontology/releases)

Normalization accepts an OBO ontology, `genes_to_phenotype.txt`, and `phenotype.hpoa` with the same
release identity. It preserves gene/disease identifiers, term names, obsolete/replacement terms,
evidence, references, frequency, qualifier, onset, sex, modifier, aspect, biocuration, license and
release provenance. Disease `NOT` annotations remain inspectable but are excluded from positive
association lookup. Gene scoring uses original/replaced term identifiers and Resnik BMA; frequency,
onset and disease evidence fields are preserved for inspection, not silently incorporated into
new scoring math.

The normalized store loads the bounded ontology into memory and accesses associations through
indexed DuckDB tables. Gene/disease lookups refuse more than 10,000 returned associations. Conversion
checks live memory and disk, uses bounded association batches, and limits DuckDB to 512 MiB/two
threads. Full public releases were not downloaded during implementation; official-shaped tiny
synthetic fixtures exercise conversion and the complete ranking workflow offline. The older
Phase 3 importer remains for compatibility; Phase 4 reproducibility uses the new locked lifecycle.

Resource licenses remain the operator's responsibility: a nonempty license reference records the
applicable terms, it does not grant rights. Keep caches, archives, receipts and normalized databases
outside Git. No gated-resource host is allow-listed.
