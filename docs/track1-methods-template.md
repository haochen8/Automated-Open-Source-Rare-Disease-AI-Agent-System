# Track 1 methods report template

This reusable draft describes the bounded computational submission route. It contains no
executed case results. Create the completed report outside this repository, replace the
bracketed fields from verified records, and retain the stated limitations. Do not add private
run paths, sample identifiers, candidate alleles, phenotype details, or case-derived metrics
to this public template.

The sections correspond to cells A7–A19 of the organizer's
[methods workbook](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/resolve/main/static/templates/methods_description_form.xlsx?download=true).
Submit the completed report as PDF or Markdown, as specified in the
[Track 1 instructions](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/submit_track1.py).
The workbook is a preparation aid. Its AI-use disclosure is required when applicable;
the other questions are voluntary, while the report itself is required by the upload form.

## A7. Team or participant name

[Hugging Face username and, if applicable, exact shared team/display name]

Repository: <https://github.com/haochen8/Automated-Open-Source-Rare-Disease-AI-Agent-System>

## A8. Model or approach number

[Submission/model number associated with this report]

Approach: deterministic bounded variant ranking with saved inheritance hypotheses and an
explicit subjective EPCR convention. Policy: `track1-computational-hypotheses-v1`.

## A9. Method

### Evidence processing and ranking

The route consumes an existing completed, integrity-checked bounded analysis. Packaging does
not perform new annotation or expand its input coverage. VCF-derived alleles and transcript
consequences are preserved in Parquet, with source mappings and evidence queried through
DuckDB. Source alleles, normalized GRCh38 representations, genotype reconciliation, resource
provenance, and committed-stage checksums remain separately inspectable. Unresolved source
binding must prevent export.

Record code identities for the saved analysis, hypothesis selector, and final export
separately: [verified revisions or source snapshots]. Reproducing an export from sealed prior
candidates does not reproduce their upstream analysis or selection. Identify any unpublished
local changes explicitly; a linked repository revision does not establish their availability.
The [public reproduction guide](track1-reproduction.md) documents source preparation, pinned
offline annotation or reuse, corpus analysis, saved-result selection, and export, with complete
data-free configuration examples. The [public selector](../scripts/track1_select.py) verifies a
pinned completed corpus and recovers source mappings and saved evidence before applying the
frozen policy; it does not perform new annotation, recompute evidence, or rerank candidates.
The [final exporter](track1-export.md) documents the sealed inputs needed for formatting.

Report these separately: [published code revision], [exact private input/resource/configuration
provenance retained locally], and [starting stage, scope, and comparison results of any empirical
reproduction]. Availability of this code route does not establish that a full raw-input-to-final
replay was performed. Preserve the original analysis identity when reusing historical results.

The deterministic ranker combines quality, population rarity, consequence, phenotype, and
inheritance features through a configured weighted linear score. Phenotype matching uses HPO
ontology and gene associations with Resnik best-match-average similarity. Population frequency
uses supported gnomAD annotation fields; caller allele frequency is not substituted. Missing
scientific evidence remains unknown even where a documented ranking baseline uses a numerical
default. The conservative source branch and rescue behavior preserve candidates lacking known
phenotype associations.

Selection uses the frozen `filtering_phenotype_inheritance` order. The workflow's 28 contexts
are correlated feature/weight sensitivity checks, not independent experiments, votes, or
replications. Neither their agreement nor the weighted score is a calibrated causal probability.

### Saved configuration and independent confirmation

Complete these declarations from the saved specification, pedigree, and receipts:

| Saved declaration | Value used by the analysis |
| --- | --- |
| `confirmed_affected_sample`, if this entry point was used | [true / false / unavailable] |
| Analyzed individual's pedigree `affected` value | [true / false / null / unavailable] |
| `sample_phenotype_linkage_confirmed`, if recorded | [true / false / unavailable] |

These are configured inputs and saved declarations. State separately whether supplied evidence
independently establishes local file-to-sample identity, phenotype linkage, and affected status:
[describe the verified basis for each, or state that it remains unconfirmed]. A saved flag named
`confirmed` is not itself independent confirmation. Do not describe a true configured flag as
false merely because its supporting evidence remains unconfirmed.

In the Phase 5 entry point, `confirmed_affected_sample=true` sets pedigree `affected=true` and
the saved linkage flag to true; false sets affected status to unknown. The inheritance evaluator
caps fit when affected status is unknown, so this configuration can affect ranking. HPO similarity
uses the supplied phenotype terms. Packaging preserves the saved evidence and order; it does not
change these declarations, recompute scores, or resolve uncertainty about the supplied inputs.

### Converting ranked alleles to submission hypotheses

The deterministic policy examines the first 100 distinct source alleles in the frozen order
and greedily emits at most ten hypotheses:

1. Skip an allele already used in an emitted hypothesis, including an equivalent normalized
   coordinate representation. Unknown or reference genotype calls do not form a hypothesis.
2. Prefer a singleton for a called homozygous-alternate or haploid-alternate genotype. Also
   permit a singleton when saved positive de novo evidence is accompanied by two called
   reference parents and a true saved `quality_checks_passed` flag. These are conditional rules;
   the policy does not establish sample identities, parentage, affected status, or causality.
3. Otherwise consider saved pairs with heterozygous calls, a shared gene and chromosome, and
   no recorded contradiction. Choose the first eligible pair in descending saved confidence,
   then ascending partner rank, then stable source-identifier order. Pair partners may lie
   outside the 100-allele anchor window. Saved confidence is an ordinal selection key.
4. Preserve the saved phase status. An unknown or possible-trans phase is not upgraded to
   confirmed trans. If no eligible pair exists, retain an explicitly exploratory singleton.
5. Consume both endpoints of an emitted pair. Do not reuse endpoints or emit coordinate
   duplicates. Duplicate comparison ignores chromosome `chr` prefixes and letter case;
   the selection audit preserves the supplied source-normalized coordinates. An optional
   [final export](track1-export.md) maps bare nuclear chromosome labels to their canonical
   `chr`-prefixed forms while preserving positions, alleles, row order, EPCR, and all other
   field values. This presentation mapping is not liftover or allele renormalization.

The implementation is [the submission policy](../scripts/track1_submission_policy.py).
The saved inheritance quality flag means that the evaluator found no failing observed genotype
quality or alternate-fraction value under its thresholds. Missing values do not fail that check;
a true flag does not establish that these measurements were available or sufficient. Pair
compatibility concerns the saved genotype, gene, chromosome, and phase fields. It does not
establish a gene-disease association, a recessive disease mechanism, or allele pathogenicity.
The organizer's public handler requires `PROBAND01` as the challenge-case label. This label
is submission metadata and does not establish which biological person a local sample represents.

### EPCR convention

EPCR means estimated probability of causal relationship. For emitted row rank `r`, use the
fixed subjective convention:

```text
p_r = 0.50 × (1 − 0.80) × 0.80^(r − 1), for r = 1, ..., n and n ≤ 10.
```

The constants express a participant-chosen allocation of uncertainty and a geometric rank
discount. They are not fitted to truth labels, calibrated against clinical outcomes, derived
from raw ranking scores, or prescribed by the organizer. With `n` rows, assigned mass is
`0.50 × (1 − 0.80^n)` and residual mass is `1 − 0.50 × (1 − 0.80^n)`. The residual covers
omitted hypotheses, explanations outside the bounded analysis, and failures of the modeling
assumptions. Do not renormalize the selected rows to sum to one.

This convention supplies an explicit, uncalibrated subjective forecast. It does not establish
empirical accuracy or a causal conclusion. The organizer
[parser](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/evaluation.py)
accepts EPCR in `(0, 1]`, orders rows by descending EPCR, and preserves input order for ties.

### Scope and limitations

This route has incomplete case coverage because it reuses a bounded analysis and further
limits hypothesis anchors. Disclose the actual saved linkage and affected-status configuration
above, separately from whether supplied evidence independently confirms it. Preserve unresolved
uncertainties without relabeling the saved configuration. Do not infer parentage, sex,
pathogenicity, or phase from missing information. No clinical truth labels are available to
validate the intended submission.
Synthetic correctness checks and correlated sensitivity contexts do not establish performance
on independent cases. The output consists of research hypotheses, not clinical findings.

## A10. Generative AI disclosure — required when applicable

Provider/product: [provider and product actually used for engineering or review]

Plan or tier: [verified account plan or API terms]

Relevant data-handling setting: **[UNVERIFIED — record the actual training/data-sharing control
and applicable retention setting; do not infer these from the subscription name.]**

AI assistance supports software engineering and method review. The selected saved-analysis
pipeline uses deterministic scientific operations and a mock planner; it performs no live
patient-data model inference. This statement describes that pipeline and does not attest to
account-wide privacy controls or the contents of other assistant sessions. Record any additional
AI use relevant to the submission accurately. Do not claim consent or compliant provider settings
without verified evidence. The organizer requires provider, tier, and relevant settings in its
[methods update](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/discussions/10).

## A11. Automated output or downstream manual curation

The described route produces the submission automatically from frozen saved rankings and
evidence using the disclosed policy. A person selects this policy and its subjective EPCR
convention. There is no manual biological selection or reranking of individual candidates.
Software review and structural checks do not constitute independent clinical interpretation.

## A12. Details of manual review

No external genetics expert review or manual biological curation is part of this route.
Engineering review concerns source binding, deterministic selection, representation,
uncertainty disclosure, and file-format validity. No expert sign-off or clinical validation
is claimed. If any candidate-level manual changes occur, revise this section and A11 to
describe them before submission.

## A13. Public or proprietary data

The challenge's genomic and phenotype inputs are gated data, not public reference data.
The scientific method is designed to use public reference resources alongside those authorized
inputs. [Confirm from the saved provenance whether any additional proprietary scientific data
were used; do not substitute an assumption.]

## A14. Public reference sources

Complete this inventory from the existing analysis receipts. The resource names below describe
the method's dependencies and do not verify the release actually used by a particular run.

| Resource | Method role | Verified release, citation, and provenance to supply |
| --- | --- | --- |
| GRCh38 reference and normalization tools | Allele representation and reference checks | [Reference source/release/checksum; tool/version] |
| Ensembl VEP and transcript annotation | Preserved transcript consequences | [Tool/cache/transcript releases; applicable source citation] |
| gnomAD fields present in the saved annotation | Population-frequency evidence | [Actual population source/version and supported field names, or unavailable] |
| Human Phenotype Ontology and gene associations | Phenotype normalization and similarity | [Locked release, source URLs, checksums, and citation] |
| Other public databases or literature, if actually used | [Specific role] | [Verified source/version/citation, or none] |

Do not imply that absent clinical classifications, predictor scores, population annotations,
or literature review were supplied. Public resource handling is documented in
[resources](resources.md); processing responsibilities are documented in
[architecture](architecture.md).

## A15. Proprietary scientific sources

[List any additional proprietary scientific data and their role, or state that none were used
only after checking provenance.] The gated challenge inputs are identified separately in A13.
The assistant subscription belongs in A10 and is not itself a biological evidence database.

## A16. Compound-heterozygous capability

The method can emit single variants and saved same-gene, same-chromosome compound-heterozygous
hypotheses. Both endpoints must have compatible heterozygous calls. It preserves saved phase
uncertainty and does not infer trans inheritance merely from two variants sharing a gene.
Pair compatibility does not establish gene-disease validity or pathogenicity of either allele.
An exploratory singleton does not assert a dominant, de novo, or recessive causal explanation.

## A17. Secondary or incidental findings

This submission route includes only primary research hypotheses. Every emitted row uses
`finding_type=primary`; no incidental candidates are added. This choice does not establish
that incidental findings are absent from the underlying data.

## A18. Runtime and cost

[In the private completed report, supply measured runtime and cost if available, identifying
hardware and which stages the measurements cover. Separate the earlier bounded analysis from
submission packaging. If measurements are unavailable, state that explicitly. Do not extrapolate
bounded processing to whole-case cost or runtime.]

## A19. Abstract — fewer than 500 words

We describe a local-first computational route for producing bounded rare-disease variant
hypotheses. The route reuses an existing completed analysis and preserves source alleles,
normalized GRCh38 representations, transcript consequences, genotype reconciliation, and
resource provenance through a VCF–Parquet–DuckDB workflow. A deterministic weighted ranker
combines quality, population rarity, consequence, HPO phenotype similarity, and inheritance
features. Missing scientific evidence remains unknown, and conservative source membership
and rescue behavior preserve candidates without known phenotype associations.

Submission selection scans the first 100 distinct source alleles in one frozen ranking context
and emits at most ten hypotheses. It conditionally prefers called homozygous-alternate,
haploid-alternate, or de novo singletons with called reference parents and a passing saved
quality flag. Missing quality measurements can still pass that flag. Otherwise, it selects
the strongest compatible saved same-gene, same-chromosome heterozygous pair using deterministic
confidence and rank ordering. Pair partners may fall outside the anchor window. Saved phase
uncertainty is preserved; an exploratory singleton is retained when no eligible pair exists.
Endpoints and equivalent normalized coordinates are not reused. All rows are primary hypotheses.

EPCR values follow the disclosed subjective convention
`p_r = 0.50 × (1 − 0.80) × 0.80^(r − 1)`. Values are not calibrated, do not convert weighted
ranking scores into measured probabilities, and are not renormalized over the selected list.
Residual mass represents omitted explanations and uncertainty in the modeling assumptions.

The approach supports reproducibility and inspectable reasoning, but coverage is bounded and
incomplete. Saved linkage and affected-status settings are disclosed separately from independent
confirmation; packaging preserves their effects and does not resolve input uncertainty. Compatible
pairs do not establish a gene-disease mechanism or allele pathogenicity.
Clinical truth labels are unavailable, and the 28 correlated ranking contexts are sensitivity
checks rather than independent validation. AI assistance supports engineering and review;
the selected pipeline performs no live patient-data model inference. No external genetics
expert review or manual biological curation is claimed. These are research hypotheses, with
no claim of empirical clinical accuracy or established causality.

## Acknowledgement — outside the abstract

[Copy the organizer-prescribed acknowledgement verbatim from the current official
[rules](https://huggingface.co/spaces/SageBio/rare-disease-real-kid-mva-hackathon-2026/blob/main/tabs/rules.py)
into this section of the completed private report. The rules require the acknowledgement for
publications, preprints, conference abstracts, and other public communications using the data.
Retain the reviewed rules revision/date in local provenance. Keep this section separate from
A19 so that it is not included in the abstract's 500-word limit. Do not substitute a paraphrase
or leave this instruction placeholder in the completed report.]
