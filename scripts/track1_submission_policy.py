"""Pure, frozen Track 1 hypothesis selection over already validated saved evidence.

The caller binds source alleles, GRCh38 coordinates, genotypes and saved evidence;
this module neither reads data nor infers phase, identity, affected status or linkage.
It scans at most 100 source-deduplicated BASE_MODE anchors and emits at most ten
research hypotheses. The 28 correlated contexts are not independent votes.

Pair confidence is an ordinal selection key, never EPCR. EPCR is an explicitly
subjective, uncalibrated rank forecast: p_r = .50 * (1 - .80) * .80**(r - 1).
Unallocated mass includes omitted and unmodeled explanations; rows are never
renormalized. Neither selection nor this allocation establishes clinical causality.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

POLICY_VERSION = "track1-computational-hypotheses-v1"
MAX_ANCHORS = 100
MAX_HYPOTHESES = 10
EPCR_Q = Decimal("0.50")
EPCR_DECAY = Decimal("0.80")
CSV_HEADERS = (
    "proband_id",
    "chrom_1",
    "pos_1",
    "ref_1",
    "alt_1",
    "chrom_2",
    "pos_2",
    "ref_2",
    "alt_2",
    "epcr",
    "finding_type",
    "notes",
)
GENOTYPES = frozenset({"heterozygous", "homozygous_alt", "haploid_alt", "reference", "unknown"})
PAIR_PHASES = frozenset({"confirmed_trans", "possible_trans", "unknown"})


class PolicyRefusal(ValueError):
    """Fixed diagnostics contain no supplied allele or case values."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PolicyRefusal(reason)


def _text(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and all(32 <= ord(char) != 127 for char in value)
    )


@dataclass(frozen=True, order=True)
class Allele:
    """Source-normalized coordinates supplied by the source-binding adapter."""

    chrom: str
    pos: int
    ref: str
    alt: str

    def __post_init__(self) -> None:
        _require(all(_text(value) for value in (self.chrom, self.ref, self.alt)), "invalid_allele")
        _require(type(self.pos) is int and self.pos > 0, "invalid_position")


def coordinate_key(allele: Allele) -> tuple[str, int, str, str]:
    """Collapse only chr-prefix/case aliases; never rewrite submitted coordinates."""
    chrom = allele.chrom[3:] if allele.chrom.lower().startswith("chr") else allele.chrom
    return chrom.upper(), allele.pos, allele.ref.upper(), allele.alt.upper()


@dataclass(frozen=True)
class Candidate:
    allele: Allele
    rank: int
    genes: tuple[str, ...]
    genotype: str
    source_id: str
    de_novo_positive: bool = False
    both_parents_called_reference: bool = False
    de_novo_quality_pass: bool = False

    def __post_init__(self) -> None:
        _require(isinstance(self.allele, Allele), "invalid_candidate_allele")
        _require(type(self.rank) is int and self.rank > 0, "invalid_frozen_rank")
        _require(_text(self.source_id), "invalid_source_id")
        _require(isinstance(self.genes, tuple) and all(map(_text, self.genes)), "invalid_genes")
        _require(self.genotype in GENOTYPES, "invalid_genotype_state")
        _require(
            all(
                type(value) is bool
                for value in (
                    self.de_novo_positive,
                    self.both_parents_called_reference,
                    self.de_novo_quality_pass,
                )
            ),
            "invalid_de_novo_flags",
        )


@dataclass(frozen=True)
class Pair:
    """One saved pair; missing/possible phase does not mean observed contradiction."""

    first: Candidate
    second: Candidate
    gene: str
    confidence: Decimal
    phase_status: str
    observed_contradiction: bool = False

    def __post_init__(self) -> None:
        _require(
            isinstance(self.first, Candidate) and isinstance(self.second, Candidate), "bad_pair"
        )
        _require(_text(self.gene) and _text(self.phase_status), "invalid_pair_evidence")
        _require(
            isinstance(self.confidence, Decimal)
            and self.confidence.is_finite()
            and 0 <= self.confidence <= 1,
            "invalid_pair_confidence",
        )
        _require(type(self.observed_contradiction) is bool, "invalid_contradiction_flag")


@dataclass(frozen=True)
class Hypothesis:
    first: Candidate
    second: Candidate | None
    reason: str
    phase_status: str | None = None
    gene: str | None = None


@dataclass(frozen=True)
class Decision:
    anchor: Candidate
    reason: str
    count: int = 1


@dataclass(frozen=True)
class Selection:
    hypotheses: tuple[Hypothesis, ...]
    audit: tuple[Decision, ...]


def _partner(anchor: Candidate, pair: Pair) -> Candidate:
    _require(anchor.source_id in {pair.first.source_id, pair.second.source_id}, "pair_not_incident")
    return pair.second if pair.first.source_id == anchor.source_id else pair.first


def pair_sort_key(anchor: Candidate, pair: Pair) -> tuple[Decimal, int, tuple[str, str]]:
    """The provider sorts by this key, then its stable saved-row identifiers.

    Unary Decimal negation uses the ambient precision, so copy_negate preserves
    the supplied ordinal confidence exactly even under a small caller context.
    """
    partner = _partner(anchor, pair)
    first, second = sorted((pair.first.source_id, pair.second.source_id))
    return pair.confidence.copy_negate(), partner.rank, (first, second)


def _singleton_reason(anchor: Candidate) -> str | None:
    if anchor.genotype == "homozygous_alt":
        return "homozygous_alternate_call"
    if anchor.genotype == "haploid_alt":
        return "haploid_alternate_call"
    if (
        anchor.de_novo_positive
        and anchor.both_parents_called_reference
        and anchor.de_novo_quality_pass
    ):
        return "saved_de_novo_with_reference_parents_and_quality"
    return None


def _pair_rejection(
    anchor: Candidate,
    partner: Candidate,
    pair: Pair,
    consumed_sources: set[str],
    consumed_coordinates: set[tuple[str, int, str, str]],
) -> str | None:
    if anchor.source_id == partner.source_id:
        return "rejected_pair_same_source_allele"
    if coordinate_key(anchor.allele) == coordinate_key(partner.allele):
        return "rejected_pair_same_coordinates"
    if (
        partner.source_id in consumed_sources
        or coordinate_key(partner.allele) in consumed_coordinates
    ):
        return "rejected_pair_consumed_endpoint"
    if coordinate_key(anchor.allele)[0] != coordinate_key(partner.allele)[0]:
        return "rejected_pair_different_chromosomes"
    if pair.gene not in anchor.genes or pair.gene not in partner.genes:
        return "rejected_pair_gene_mismatch"
    if anchor.genotype != "heterozygous" or partner.genotype != "heterozygous":
        return "rejected_pair_nonheterozygous_call"
    if pair.observed_contradiction:
        return "rejected_pair_observed_contradiction"
    if pair.phase_status not in PAIR_PHASES:
        return "rejected_pair_unsupported_phase"
    return None


def select_hypotheses(
    anchors: Sequence[Candidate],
    pairs_for_anchor: Callable[[Candidate], Iterable[Pair]],
    limit: int = MAX_HYPOTHESES,
) -> Selection:
    """Scan fixed ranks, preferring specified singletons then the first eligible pair.

    The caller supplies at most 100 anchors in ascending frozen BASE_MODE rank;
    partners can lie outside that anchor window. The pair provider streams saved
    rows in pair_sort_key order from bounded pages. Only the examined prefix is
    order-checked: selection stops immediately on its first eligible pair. No
    incident-pair list is buffered or sorted here. Rejected-pair audit counts are
    bounded by fixed reason categories, not the potentially large pair count.

    Missing/reference calls and rejected pair records are audited as unavailable
    hypotheses, never as evidence excluding a disease. Invalid source binding is
    the adapter's fail-closed responsibility. No unaffected-status, phenotype
    linkage, phase or independent evidence is inferred by this policy.
    """
    _require(type(limit) is int and 1 <= limit <= MAX_HYPOTHESES, "invalid_hypothesis_limit")
    _require(len(anchors) <= MAX_ANCHORS, "anchor_window_exceeded")
    _require(all(isinstance(anchor, Candidate) for anchor in anchors), "invalid_anchor")
    _require(
        all(left.rank <= right.rank for left, right in zip(anchors, anchors[1:], strict=False)),
        "anchors_not_in_frozen_rank_order",
    )
    consumed_sources: set[str] = set()
    consumed_coordinates: set[tuple[str, int, str, str]] = set()
    hypotheses: list[Hypothesis] = []
    audit: list[Decision] = []
    for anchor in anchors:
        if (
            anchor.source_id in consumed_sources
            or coordinate_key(anchor.allele) in consumed_coordinates
        ):
            audit.append(Decision(anchor, "skipped_consumed_endpoint"))
            continue
        if anchor.genotype in {"reference", "unknown"}:
            audit.append(Decision(anchor, f"unavailable_{anchor.genotype}_genotype"))
            continue
        reason = _singleton_reason(anchor)
        hypothesis = None
        if reason is not None:
            hypothesis = Hypothesis(anchor, None, reason)
        else:
            rejected: Counter[str] = Counter()
            previous_key = None
            for pair in pairs_for_anchor(anchor):
                key = pair_sort_key(anchor, pair)
                _require(previous_key is None or previous_key <= key, "pair_stream_not_ordered")
                previous_key = key
                partner = _partner(anchor, pair)
                rejection = _pair_rejection(
                    anchor, partner, pair, consumed_sources, consumed_coordinates
                )
                if rejection is not None:
                    rejected[rejection] += 1
                    continue
                hypothesis = Hypothesis(
                    anchor,
                    partner,
                    "saved_compound_heterozygous_hypothesis",
                    pair.phase_status,
                    pair.gene,
                )
                break
            audit.extend(Decision(anchor, reason, rejected[reason]) for reason in sorted(rejected))
            if hypothesis is None:
                hypothesis = Hypothesis(
                    anchor, None, "exploratory_singleton_no_eligible_saved_pair"
                )
        hypotheses.append(hypothesis)
        audit.append(Decision(anchor, hypothesis.reason))
        for endpoint in (hypothesis.first, hypothesis.second):
            if endpoint is not None:
                consumed_sources.add(endpoint.source_id)
                consumed_coordinates.add(coordinate_key(endpoint.allele))
        if len(hypotheses) == limit:
            break
    return Selection(tuple(hypotheses), tuple(audit))


def rank_epcr(rank: int) -> Decimal:
    """Fixed subjective rank mass, independent of raw scores or row count."""
    _require(type(rank) is int and 1 <= rank <= MAX_HYPOTHESES, "invalid_epcr_rank")
    with localcontext() as context:
        context.prec = 32
        return EPCR_Q * (1 - EPCR_DECAY) * EPCR_DECAY ** (rank - 1)


def residual_mass(count: int) -> Decimal:
    """Mass left for omitted/unmodeled explanations; it is never redistributed."""
    _require(type(count) is int and 0 <= count <= MAX_HYPOTHESES, "invalid_hypothesis_count")
    with localcontext() as context:
        context.prec = 32
        return 1 - sum((rank_epcr(rank) for rank in range(1, count + 1)), Decimal(0))


def render_csv(selection: Selection, proband_id: str) -> bytes:
    """Render exact submission columns; identifier is a label, not inferred identity."""
    _require(_text(proband_id), "invalid_submission_identifier")
    _require(1 <= len(selection.hypotheses) <= MAX_HYPOTHESES, "invalid_submission_row_count")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    for rank, hypothesis in enumerate(selection.hypotheses, 1):
        endpoints: list[str | int] = []
        for candidate in (hypothesis.first, hypothesis.second):
            if candidate is None:
                endpoints.extend(("", "", "", ""))
            else:
                allele = candidate.allele
                endpoints.extend((allele.chrom, allele.pos, allele.ref, allele.alt))
        notes = (
            f"Research hypothesis: {hypothesis.reason}; "
            f"saved phase={hypothesis.phase_status or 'not_applicable'}; "
            "subjective uncalibrated rank EPCR (q=0.50,d=0.80); "
            "top-100 BASE_MODE anchor bound; 28 correlated contexts, no vote; "
            "linkage and affected-status uncertainty retained"
        )
        writer.writerow((proband_id, *endpoints, format(rank_epcr(rank), "f"), "primary", notes))
    return stream.getvalue().encode("utf-8")
