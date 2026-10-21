# Track 1 organizer evidence and evaluation readiness

Public-documentation review: 2026-10-03, updated 2026-10-20. This records the organizer's stated contract,
not patient results, verified local-file identity, or independent accuracy. No gated
payload, hidden answer, leaderboard submission or private artifact was inspected.

## Evidence established by public documentation

| Question | Public evidence | Remaining limitation |
| --- | --- | --- |
| Are genomic and phenotype inputs intended to describe the same case? | The [overview](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/about.py) describes Track 1 as using the subject's genomic data and symptom description. The [rules](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/rules.py) identify a single-subject dataset. | Organizer-declared association does not bind local files or a VCF sample column to that subject. No versioned file-to-sample mapping was established by this review. |
| Is affected status supplied? | The overview explicitly describes the challenge subject as affected. | This is a declaration about the challenge subject, not evidence of status for every sample or relative. Local sample identity remains a separate requirement. |
| Does a validated answer exist? | The rules describe NHS-validated causal variant(s), and the [FAQ](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/faq.py) describes scoring against one clinically confirmed answer. | This is organizer-stated provenance. No usable local truth labels or separate participant validation cohort were established in the reviewed documentation. Their absence from this review is not proof that they do not exist. |
| What does the challenge score? | The overview and FAQ specify rank points and F-max, with partial credit for one member of a pair. Methods receive a separate qualitative review. | These differ from this repository's gene, allele and allele/gene MRR and Recall@k. For two-allele labels, allele and allele/gene retrieval require both members; gene retrieval assesses the expected gene. Neither metric set establishes performance across independent cases. |
| What is the submission unit? | The [Track 1 instructions](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/submit_track1.py) specify at most ten candidate rows, one variant or pair per row, GRCh38 coordinates, EPCR, and primary/secondary classification. | A schema-valid export is not a verified prediction. Heuristic ranking scores are not calibrated causal probabilities. A separately disclosed subjective EPCR convention can support an authorized computational draft; it does not validate a score-to-probability conversion or authorize submission. |

The [dataset card](https://huggingface.co/datasets/SageBio/mva-hackathon-2026-data)
provides access/download instructions. It does not supply the missing local truth manifest.
The Space's basic web view exposed only an application shell, so this audit read the
public source of its informational tabs without executing the application. The source
listing showed revision `c9b4a7e`; links above track `main` and may change. A future
execution decision should preserve exact revision and content identities in its own
provenance record.

## Recommended sequence

1. Review local provenance within an explicitly authorized private scope. Bind exported
   alleles to the source and verified normalized representation. Establish file-to-subject
   and VCF sample mappings only from supplied evidence; unresolved phenotype linkage,
   affected status, sex, and parentage remain unknown. A computational draft may disclose
   these limitations without claiming they are resolved. Keep private metadata outside
   Git and model context.
2. Use legitimately available truth labels and explicit source-allele mappings for any
   accuracy assessment. If truth labels are unavailable, keep real-case accuracy pending;
   this does not prohibit a clearly labeled computational draft. Do not recover hidden
   answers, treat leaderboard feedback as a tuning set, or infer labels from the case description.
3. Use the existing [local evaluator](case-evaluation.md) with a frozen manifest,
   failed/unavailable cases included, and distinct development/evaluation cases.
   Preserve gene, allele and allele/gene denominators and require both labeled pair
   members. Do not describe this as the official scoring implementation.
4. Continue synthetic correctness checks for ambiguous mappings, inconsistent persisted
   rankings, missing evidence, and failure/resume. Such checks improve software
   readiness; they do not replace the missing scientific evaluation evidence.
5. For an authorized local computational draft, review representation, source binding,
   coverage, preserved uncertainty, and the exact selection/EPCR policy. Outside clinical
   review is not an organizer prerequisite identified in the reviewed materials; do not
   claim such review occurred. The [frozen policy](../scripts/track1_submission_policy.py)
   and [methods template](track1-methods-template.md) document the limited route below.
   Publication and challenge submission remain separate actions. This documentation
   review does not establish clinical validity or satisfy unrelated gates in the
   [Phase 6 proposal](phase6-recommendation.md).

The repository's privacy policy continues to apply. Public submission instructions do
not authorize publishing private runs, patient-derived metrics or reports to Git.

## Provisional local CSV format check

`scripts/track1_validate.py` checks an explicitly supplied CSV against
`track1-provisional-local-v1`. It is a standalone standard-library tool: it does not load
saved analyses, choose candidates, resolve source alleles, generate probabilities, write
reports or submit files. Keep real candidate files outside Git and use this tool only
within an authorized private-data scope.

```sh
.venv/bin/python scripts/track1_validate.py \
  --input /absolute/private/path/candidates.csv \
  --expected-proband SUPPLIED_LABEL \
  --order-policy descending-epcr-preserve-input-ties
```

The interpreter path is an example; select an existing environment explicitly. The
expected proband label and row order are supplied decisions, not inferred sample linkage
or scientific recommendations. The validator requires the exact twelve-column template
header, one to ten rows, complete first-variant fields, an entirely empty or complete
second-variant tuple, positive ASCII-decimal integer positions, distinct pair endpoints
and no duplicate singletons or unordered pairs. Chromosome labels are compared literally;
positions are compared numerically and allele letters case-insensitively for duplicate
checks only. Structural fields and the supplied proband label reject surrounding
whitespace and ASCII control characters. Notes may contain quoted line breaks; NUL is
rejected in every field.
EPCR must remain finite and in `(0, 1]` when parsed as a floating-point value, including
rejection of overflow or underflow to zero. Rows must already be in descending EPCR
order. Equal values retain their supplied input order. It does not
impose an EPCR sum rule, calibrate values, reorder rows, normalize alleles or silently
default the required `primary`/`secondary` classification.

Input is limited to a stable regular file of at most 1 MiB, with at most 8 KiB per UTF-8
field. These are local engineering bounds. Successful validation returns exit code 0
and a fixed JSON status: format validation is `valid`, the official contract is
`unverified`, and source binding, coverage and submission readiness are `not_assessed`.
Refusal returns exit code 2 and the fixed error `TRACK1_FORMAT_VALIDATION_REFUSED`, without
candidate values or raw exceptions. No output files are created.

The public-source review on 2026-10-19 found the
[template header](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/static/templates/track1_submission_template.csv)
and the parser's EPCR range and stable ordering in
[evaluation.py](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/evaluation.py).
The Space listing displayed revision `60e1c30e337ce6fe01813040dbe49979eb8c50aa`, but the
review used rendered `main` pages and did not verify exact revision bytes. The local
contract deliberately rejects incomplete pairs, duplicate candidates and missing
classification that the public parser can accept or alter. A local pass therefore does
not establish exact organizer-parser equivalence or official compliance.

Before packaging actual predictions, review source-allele mappings, build provenance,
coverage, candidate interpretation and EPCR justification. These checks do not imply an
external expert has reviewed the biological conclusions. The dated public audit below
supersedes the earlier uncertainty about the methods workbook's questions. A valid CSV
alone does not establish submission readiness.

## Public requirements checked on 2026-10-20

The [closing announcement](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/discussions/28)
sets the deadline at **2026-10-24 23:59 UTC**. This is **2026-10-25 01:59 in
Europe/Stockholm**. Track 1 has no ranked prizes; teams earning 100 rank points on their
best final entry are offered consortium authorship, and either track is eligible for the
Innovation Prize. This describes the organizer's recognition policy, not a result for this project.

The public [Track 1 form](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/submit_track1.py)
requires HF sign-in, a GitHub repository URL, a predictions CSV, and a PDF or Markdown
methods report. Team/display name is optional. It explicitly identifies `PROBAND01` as
the accepted challenge-case label. Using that label does not establish local sample identity
or phenotype linkage. Prediction filenames should identify the participant/team and approach;
report filenames should identify the participant/team.

The [FAQ](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/faq.py)
and [rules](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/rules.py)
permit individual participation, require participants to be at least 18, and require each
team member to register and accept the rules. Registration includes a valid email and a gated
dataset-access request. Track 1 permits six submissions per participant. No outside genetics
expert sign-off requirement was found in these materials or the methods template.

The repository must contain documented reproducible code and configuration. It may remain
private during the event and normally must be public afterward; prediction files are optional
in the repository, and the methods report is recommended there. A later
[organizer response](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/discussions/31)
offers Synapse as an alternative for a team unable to use GitHub, but the public form still
validates a GitHub URL. That alternative's upload mechanics are not established by this audit.
An [incidental-findings discussion](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/discussions/20)
remains referred to privacy/IRB review, with interim advice to keep the affected repository
private. General publication language does not clear incidental findings for disclosure.

The [rules](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/rules.py)
also prescribe an acknowledgement for publications, preprints, conference abstracts, and other
public communications using the data. Copy the required wording verbatim into the completed
report's acknowledgement section, outside the A19 abstract. The
[methods template](track1-methods-template.md#acknowledgement--outside-the-abstract) reserves that
section without reproducing the external passage here.

Video remains a documentation ambiguity. The general rules describe a three-minute recording
for every team. The [track overview](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/about.py)
lists video for Track 2, while the Track 1 form has no video input. The inspected Track 1
upload interface therefore does not require video, but an authoritative exemption from the
general wording was not found.

There is also a scoring discrepancy: prose says secondary findings will not affect automated
scores, whereas the inspected [evaluator](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/evaluation.py)
treats `finding_type` as informational and does not exclude secondary rows from scoring.
Do not claim organizer-parser equivalence from prose alone. The local policy below emits only
primary research hypotheses.

## Verified methods template questions

The public blank [methods workbook](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/resolve/main/static/templates/methods_description_form.xlsx?download=true)
was downloaded on 2026-10-20 and read as ZIP/XML without executing workbook content.
It contained 14,784 bytes with SHA-256
`61aab080a2868a3b724e76692b83c24812112e305cd3a8b03f8f91a6b2414441`.
This checksum identifies the audited public template, not any private analysis or submission.
Rendered source pages tracked `main`; the Space listing showed revision
`60e1c30e337ce6fe01813040dbe49979eb8c50aa`, but exact revision bytes were not independently verified.

The `Track 1 methods` sheet asks for the following, paraphrased:

| Cells | Requested information |
| --- | --- |
| A7–A8 | Team name and model/approach number |
| A9 | Detailed method |
| A10 | Required commercial generative-AI disclosure: provider, plan/tier, relevant data-handling setting |
| A11–A12 | Automated output versus manual curation; details of any manual review |
| A13–A15 | Public versus proprietary data and details of each |
| A16 | Compound-heterozygous pairs versus singleton capability |
| A17 | Secondary/incidental-finding handling |
| A18 | Runtime and cost estimate, if available |
| A19 | Abstract of at most 500 words, covering method, strengths, and limitations |

The workbook prefers automated computational output. Its introduction calls the other
questions voluntary; A10 is explicitly marked required. The upload form nevertheless requires
a report. The [methods announcement](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/discussions/10)
requires the latest disclosure, and the report upload accepts PDF or Markdown rather than XLSX.
Subscription names do not establish training, data-sharing, or retention settings; unknown
settings must remain unverified until the participant supplies them.

## Disclosed computational-draft route

The [submission policy](../scripts/track1_submission_policy.py) uses the frozen
`filtering_phenotype_inheritance` order and at most 100 distinct source-allele anchors to
emit at most ten hypotheses. Called homozygous-alternate or haploid-alternate genotypes,
or explicit positive de novo evidence with called reference parents and a passing saved quality
flag, can produce preferred singletons. Otherwise it chooses the first compatible saved
same-gene/chromosome heterozygous pair by descending confidence, partner rank, and stable
source identifiers. A partner may lie beyond the anchor window. Saved phase uncertainty
is preserved; absence of an eligible pair permits an explicitly exploratory singleton.
Endpoints and equivalent normalized coordinates are not reused. All rows are primary.
The inherited quality flag rejects failing observed measurements but allows missing genotype
quality and allele-fraction values. A passing flag therefore does not establish complete quality
evidence. Pair compatibility does not establish a gene-disease association, recessive disease
mechanism, or allele pathogenicity.

The [public selector](../scripts/track1_select.py) applies that policy to immutable saved
source mappings and evidence recovered from a pinned completed corpus. It verifies the saved
journal chain, then reproduces selection and EPCR allocation without new annotation, scientific
evidence computation, or reranking. The [reproduction guide](track1-reproduction.md) connects
the public source-accounting, materialization, annotation/corpus, selector, and export commands
and supplies complete data-free configuration examples. Authentic inputs, resource receipts,
saved declarations, and historical code identities must still be supplied privately.

The [final exporter](track1-export.md) applies an explicit `chr` presentation mapping to a
sealed draft after checking its agreement with the selection audit. It retains the audit's
source coordinates and preserves nonchromosome CSV fields. Reproducing this stage does not
reproduce or independently validate the preceding analysis or selection. Distinguish code
availability, exact private provenance, and an empirically completed replay from a stated
starting stage. This documentation does not claim that a complete raw-input-to-final replay
has been performed. Local changes remain unpublished until the linked repository contains them.

For output rank `r`, the policy assigns `p_r = 0.50 × (1 − 0.80) × 0.80^(r − 1)`.
This is an explicitly subjective, uncalibrated EPCR forecast, not a conversion of heuristic
scores into measured causal probabilities. Selected rows are not renormalized. Residual mass
covers omitted explanations and uncertainty in the modeling assumptions. The constants are
participant policy choices; the organizer does not prescribe them. The public parser requires
EPCR in `(0, 1]`, orders descending with stable ties, and imposes no sum-to-one rule.

This route does not require outside expert review or manual biological curation, and claims
neither. It can reuse an authorized bounded completed analysis while retaining incomplete
coverage and unresolved sample/phenotype linkage or affected status. A completed report must
disclose the actual saved configuration and pedigree flags separately from their independently
verified basis. In Phase 5, `confirmed_affected_sample` controls the analyzed individual's
affected status and saved linkage declaration; unknown affected status caps inheritance fit.
An unconfirmed basis does not mean the saved analysis used false or unknown flags. Packaging
preserves the resulting evidence and ranking without recomputation. It does not invent
annotations, truth labels, or scientific evidence. The 28 ranking contexts are correlated
sensitivity checks, not independent validation. Actual use, measured results, private
provenance, and candidate contents belong only in the separately authorized private package;
this public documentation does not attest that a particular package has been executed or submitted.
