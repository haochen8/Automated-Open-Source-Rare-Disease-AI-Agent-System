# Phase 5 scaling preparation

## Compact ranking storage

Fresh databases store ranking values with numeric candidate and ranking-context keys. The
`ranked_evidence` view retains the previous columns, candidate IDs, feature JSON and provenance
interface used by reports and exports. A context stores one provenance record per run and mode.
All four ablations in one ranking call publish in a single transaction. Re-ranking replaces each
whole mode, removing stale rows while preserving other contexts. Failed re-ranking rolls back the
scores and provenance together. Existing table-based databases remain readable and are never
migrated in place. An attached historical database does not prevent fresh local storage creation.

A generated benchmark with 5,000 synthetic candidates and 140,000 ranking rows compared the old
implementation at commit `6147dc5` with compact storage. Database size decreased from 18,886,656 to
7,614,464 bytes. Candidate IDs, ordering, scores, features and stable provenance fields matched
exactly. This isolated synthetic result is not a full-case storage or runtime forecast. Private
replays and resource measurements must remain outside Git.

## Reference-name planning

Coverage can be reviewed with the already available VEP cache's `chr_synonyms.txt`, pinned by
checksum. The planner follows publisher-supplied relationships transitively and requires a unique
local FASTA target with the expected length. Missing reference, missing annotation cache,
length mismatch and ambiguous target remain distinct. Colliding source contigs are rejected.

An explicit planning option also considers `chr` prefix conventions and `M`/`MT`, consistent with
the naming conventions handled by the installed VEP parser. These remain proposals: the planner
does not rename sequences, change coordinates, validate sequence equivalence, infer sex, or extend
inheritance-model coverage. Record-level REF checks and genomic context validation are required
before any proposed mapping is used for analysis. No additional reference download is implied.

## Remaining expansion gates

Use a fresh private directory for a larger autosomal benchmark, retaining every original ordinal
and split-allele identity. Keep live memory/disk limits and the original inputs unchanged. Measure
actual annotation, candidate expansion, database/index overhead and pair workload before budgeting
whole-input processing. A bounded result cannot establish whole-case completeness, and decoy,
sex-chromosome and unannotated-contig records must remain recoverable with explicit limitations.
