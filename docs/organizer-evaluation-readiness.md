# Track 1 organizer evidence and evaluation readiness

Public-documentation review: 2026-10-03. This records the organizer's stated contract,
not patient results, verified local-file identity, or independent accuracy. No gated
payload, hidden answer, leaderboard submission or private artifact was inspected.

## Evidence established by public documentation

| Question | Public evidence | Remaining limitation |
| --- | --- | --- |
| Are genomic and phenotype inputs intended to describe the same case? | The [overview](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/about.py) describes Track 1 as using the subject's genomic data and symptom description. The [rules](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/rules.py) identify a single-subject dataset. | Organizer-declared association does not bind local files or a VCF sample column to that subject. No versioned file-to-sample mapping was established by this review. |
| Is affected status supplied? | The overview explicitly describes the challenge subject as affected. | This is a declaration about the challenge subject, not evidence of status for every sample or relative. Local sample identity remains a separate requirement. |
| Does a validated answer exist? | The rules describe NHS-validated causal variant(s), and the [FAQ](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/faq.py) describes scoring against one clinically confirmed answer. | This is organizer-stated provenance. No usable local truth labels or separate participant validation cohort were established in the reviewed documentation. Their absence from this review is not proof that they do not exist. |
| What does the challenge score? | The overview and FAQ specify rank points and F-max, with partial credit for one member of a pair. Methods receive a separate qualitative review. | These differ from this repository's gene, allele and allele/gene MRR and Recall@k. For two-allele labels, allele and allele/gene retrieval require both members; gene retrieval assesses the expected gene. Neither metric set establishes performance across independent cases. |
| What is the submission unit? | The [Track 1 instructions](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/submit_track1.py) specify at most ten candidate rows, one variant or pair per row, GRCh38 coordinates, EPCR, and primary/secondary classification. | A schema-valid export is not a verified prediction. Heuristic ranking scores are not calibrated causal probabilities. No automatic score-to-EPCR conversion or submission is justified. |

The [dataset card](https://huggingface.co/datasets/SageBio/mva-hackathon-2026-data)
provides access/download instructions. It does not supply the missing local truth manifest.
The Space's basic web view exposed only an application shell, so this audit read the
public source of its informational tabs without executing the application. The source
listing showed revision `c9b4a7e`; links above track `main` and may change. A future
execution decision should preserve exact revision and content identities in its own
provenance record.

## Recommended sequence

1. Establish local input provenance separately. Match the original genomic input and
   phenotype document to an authoritative versioned dataset manifest, then establish
   the relevant VCF sample mapping. Do not change linkage or affected-status flags
   merely because the overview describes one subject. Any private metadata review
   must keep its results outside Git and model context.
2. Obtain legitimately available truth labels and explicit source-allele mappings.
   If unavailable, keep real-case accuracy pending. Do not recover hidden answers,
   treat leaderboard feedback as a tuning set, or infer labels from the case description.
3. Use the existing [local evaluator](case-evaluation.md) with a frozen manifest,
   failed/unavailable cases included, and distinct development/evaluation cases.
   Preserve gene, allele and allele/gene denominators and require both labeled pair
   members. Do not describe this as the official scoring implementation.
4. Continue synthetic correctness checks for ambiguous mappings, inconsistent persisted
   rankings, missing evidence, and failure/resume. Such checks improve software
   readiness; they do not replace the missing scientific evaluation evidence.
5. Prepare a local export only after candidate coverage, representation, sample mapping
   and interpretation are reviewed. Publication and challenge submission remain
   separate actions. The [Phase 6 plan](phase6-recommendation.md) is still a proposal;
   this documentation review does not satisfy its entry gate.

The repository's privacy policy continues to apply. Public submission instructions do
not authorize publishing private runs, patient-derived metrics or reports to Git.
