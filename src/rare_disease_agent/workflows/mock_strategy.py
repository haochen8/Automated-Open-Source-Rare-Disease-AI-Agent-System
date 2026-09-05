"""Deterministic Phase 2 agent script used for CI and demonstrations."""

from __future__ import annotations

from rare_disease_agent.agents.schemas import AgentDecision


def default_mock_decisions() -> list[AgentDecision]:
    """Return a safe multi-branch plan; thresholds are selected by the mock agent."""

    common = {
        "risk_of_false_negative": "low",
        "expected_effect": "Preserve the source branch while gathering or refining evidence.",
    }
    return [
        AgentDecision(
            action="inspect_statistics",
            rationale="Inspect aggregate annotations before choosing any filtering threshold.",
            parameters={"branch": "all"},
            **common,
        ),
        AgentDecision(
            action="create_rescue_branch",
            rationale="Keep a conservative reversible copy before applying rarity filtering.",
            parameters={"source_branch": "all", "target_branch": "conservative"},
            **common,
        ),
        AgentDecision(
            action="create_rescue_branch",
            rationale="Create an independent branch for molecular-consequence evidence.",
            parameters={"source_branch": "all", "target_branch": "pathogenicity"},
            **common,
        ),
        AgentDecision(
            action="create_rescue_branch",
            rationale="Create a phenotype-ready branch using supplied synthetic priorities only.",
            parameters={"source_branch": "all", "target_branch": "phenotype_ready"},
            **common,
        ),
        AgentDecision(
            action="filter_frequency",
            rationale=(
                "Rare disease candidates are usually uncommon, while missing frequency and "
                "pathogenic ClinVar evidence must be preserved."
            ),
            parameters={
                "source_branch": "conservative",
                "target_branch": "conservative_rare",
                "maximum_allele_frequency": 0.01,
                "preserve_pathogenic_clinvar": True,
            },
            expected_effect=(
                "Remove common records while retaining unknown-frequency and rescue evidence."
            ),
            risk_of_false_negative="medium",
        ),
        AgentDecision(
            action="filter_consequence",
            rationale=(
                "Retain independently the consequences most likely to alter protein function."
            ),
            parameters={
                "source_branch": "pathogenicity",
                "target_branch": "pathogenicity_impact",
                "allowed_consequences": [
                    "frameshift_variant",
                    "missense_variant",
                    "splice_acceptor_variant",
                    "splice_donor_variant",
                    "stop_gained",
                ],
            },
            expected_effect="Create a high-impact branch without deleting the conservative branch.",
            risk_of_false_negative="medium",
        ),
        AgentDecision(
            action="filter_gene",
            rationale="Use only the supplied synthetic phenotype-ready gene priorities.",
            parameters={
                "source_branch": "phenotype_ready",
                "target_branch": "phenotype_priority",
                "genes": ["SYN_CAUSAL", "SYN_ALT"],
            },
            expected_effect="Create a synthetic phenotype-priority branch for later union.",
            risk_of_false_negative="high",
        ),
        AgentDecision(
            action="merge_branches",
            rationale=(
                "Union independent strategies so no single filtering chain controls survival."
            ),
            parameters={
                "branches": [
                    "conservative_rare",
                    "pathogenicity_impact",
                    "phenotype_priority",
                ],
                "target_branch": "ensemble",
            },
            expected_effect=(
                "Produce a defensible union of rare, damaging, and priority-gene candidates."
            ),
            risk_of_false_negative="low",
        ),
        AgentDecision(
            action="stop",
            rationale=(
                "Further reduction is not justified without phenotype or inheritance evidence."
            ),
            parameters={"branch": "ensemble", "reason": "candidate_set_sufficient"},
            expected_effect="Stop safely with the reversible ensemble candidate set.",
            risk_of_false_negative="low",
        ),
    ]


def phase3_mock_decisions() -> list[AgentDecision]:
    """Safe evidence-first synthetic plan without hard-coded phenotype associations."""

    common = {
        "risk_of_false_negative": "low",
        "expected_effect": "Gather deterministic evidence without changing source candidates.",
    }
    return [
        AgentDecision(
            action="inspect_statistics",
            rationale="Inspect aggregate variant annotations before selecting branches.",
            parameters={"branch": "all"},
            **common,
        ),
        AgentDecision(
            action="get_patient_hpo_summary",
            rationale="Validate and normalize the supplied HPO identifiers before scoring.",
            parameters={},
            **common,
        ),
        AgentDecision(
            action="rank_genes_by_phenotype",
            rationale="Inspect deterministic ontology-aware gene phenotype evidence.",
            parameters={"branch": "all", "limit": 10},
            **common,
        ),
        AgentDecision(
            action="evaluate_inheritance",
            rationale="Inspect inheritance hypotheses using only pedigree genotype evidence.",
            parameters={"branch": "all"},
            **common,
        ),
        AgentDecision(
            action="create_rescue_branch",
            rationale="Keep a reversible conservative branch before rarity filtering.",
            parameters={"source_branch": "all", "target_branch": "conservative"},
            **common,
        ),
        AgentDecision(
            action="filter_frequency",
            rationale=(
                "Create a rare branch while rescuing missing frequency and pathogenic records."
            ),
            parameters={
                "source_branch": "conservative",
                "target_branch": "conservative_rare",
                "maximum_allele_frequency": 0.01,
                "preserve_pathogenic_clinvar": True,
            },
            expected_effect="Prioritize rare candidates without deleting the conservative source.",
            risk_of_false_negative="medium",
        ),
        AgentDecision(
            action="create_rescue_branch",
            rationale="Keep an independent branch for molecular consequence evidence.",
            parameters={"source_branch": "all", "target_branch": "pathogenicity"},
            **common,
        ),
        AgentDecision(
            action="filter_consequence",
            rationale="Create a high-impact branch without treating consequence as proof.",
            parameters={
                "source_branch": "pathogenicity",
                "target_branch": "pathogenicity-priority",
                "allowed_consequences": [
                    "frameshift_variant",
                    "missense_variant",
                    "splice_acceptor_variant",
                    "splice_donor_variant",
                    "stop_gained",
                ],
            },
            expected_effect="Create a reversible molecular-impact priority branch.",
            risk_of_false_negative="medium",
        ),
        AgentDecision(
            action="create_evidence_branches",
            rationale=(
                "Use evaluated evidence to create phenotype and inheritance priorities plus a "
                "non-destructive novel-gene rescue."
            ),
            parameters={
                "branch": "all",
                "phenotype_branch": "phenotype-priority",
                "inheritance_branch": "inheritance-priority",
                "novel_gene_branch": "novel-gene-rescue",
            },
            expected_effect="Create three reversible evidence branches from the full source.",
            risk_of_false_negative="low",
        ),
        AgentDecision(
            action="merge_branches",
            rationale="Union independent evidence and rescue strategies before ranking.",
            parameters={
                "branches": [
                    "conservative_rare",
                    "phenotype-priority",
                    "inheritance-priority",
                    "pathogenicity-priority",
                    "novel-gene-rescue",
                ],
                "target_branch": "ensemble",
            },
            expected_effect="Create the final inclusive candidate ensemble.",
            risk_of_false_negative="low",
        ),
        AgentDecision(
            action="stop",
            rationale="Proceed to deterministic ranking; further hard filtering is unjustified.",
            parameters={"branch": "ensemble", "reason": "evidence_ready_for_ranking"},
            expected_effect="Stop agent filtering and hand candidates to deterministic ranking.",
            risk_of_false_negative="low",
        ),
    ]
