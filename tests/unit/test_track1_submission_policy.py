"""Policy regression cases use invented alleles, genes, source IDs and case labels."""

from __future__ import annotations

import csv
import importlib.util
import io
import sys
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = load_script("track1_submission_policy")
validator = load_script("track1_validate")


def candidate(rank, *, genotype="heterozygous", source_id=None, **kwargs):
    return policy.Candidate(
        allele=policy.Allele("chrSYNTHETIC", 100 + rank, "A", "T"),
        rank=rank,
        genes=("SYNTHETIC_GENE_A", "SYNTHETIC_GENE_B"),
        genotype=genotype,
        source_id=source_id or f"SYNTHETIC_r{rank}_a1",
        **kwargs,
    )


def pair(first, second, confidence="0.5", *, phase="unknown", **kwargs):
    return policy.Pair(first, second, "SYNTHETIC_GENE_A", Decimal(confidence), phase, **kwargs)


def provider(*pairs):
    def for_anchor(anchor):
        incident = [
            item
            for item in pairs
            if anchor.source_id in {item.first.source_id, item.second.source_id}
        ]
        return iter(sorted(incident, key=lambda item: policy.pair_sort_key(anchor, item)))

    return for_anchor


def test_frozen_anchor_order_consumes_pair_endpoints_and_rejects_reuse():
    first, second, third, fourth = [candidate(rank) for rank in range(1, 5)]
    selection = policy.select_hypotheses(
        [first, second, third, fourth], provider(pair(first, third), pair(first, fourth))
    )
    assert [
        (row.first.rank, row.second.rank if row.second else None) for row in selection.hypotheses
    ] == [
        (1, 3),
        (2, None),
        (4, None),
    ]
    assert any(
        item.anchor == third and item.reason == "skipped_consumed_endpoint"
        for item in selection.audit
    )
    assert any(item.reason == "rejected_pair_consumed_endpoint" for item in selection.audit)


def test_pair_confidence_then_partner_frozen_rank_then_source_ids_determine_selection():
    anchor, early, late = [candidate(rank) for rank in (1, 2, 3)]
    selected = policy.select_hypotheses(
        [anchor], provider(pair(anchor, early, "0.2"), pair(anchor, late, "0.8"))
    )
    assert selected.hypotheses[0].second == late
    tied = policy.select_hypotheses([anchor], provider(pair(anchor, late), pair(anchor, early)))
    assert tied.hypotheses[0].second == early
    other = replace(late, rank=early.rank, source_id="SYNTHETIC_r0_a1")
    tied_ids = policy.select_hypotheses(
        [anchor], provider(pair(anchor, early), pair(anchor, other))
    )
    assert tied_ids.hypotheses[0].second == other


@pytest.mark.parametrize("phase", ["unknown", "possible_trans", "confirmed_trans"])
def test_saved_phase_is_retained_without_new_phase_inference(phase):
    first, second = candidate(1), candidate(2)
    selection = policy.select_hypotheses([first], provider(pair(second, first, phase=phase)))
    hypothesis = selection.hypotheses[0]
    assert hypothesis.first == first and hypothesis.second == second
    assert hypothesis.phase_status == phase
    assert hypothesis.reason == "saved_compound_heterozygous_hypothesis"


@pytest.mark.parametrize("genotype", ["homozygous_alt", "haploid_alt"])
def test_alternate_singleton_exception_does_not_consult_pair_provider(genotype):
    def unused(_):
        pytest.fail("singleton evidence must take precedence")

    selection = policy.select_hypotheses([candidate(1, genotype=genotype)], unused)
    assert selection.hypotheses[0].second is None
    assert selection.hypotheses[0].reason in {"homozygous_alternate_call", "haploid_alternate_call"}


def test_positive_saved_de_novo_requires_reference_parents_and_passing_quality():
    flags = {
        "de_novo_positive": True,
        "both_parents_called_reference": True,
        "de_novo_quality_pass": True,
    }
    partner = candidate(2)
    positive = candidate(1, **flags)
    selection = policy.select_hypotheses([positive], provider(pair(positive, partner)))
    assert selection.hypotheses[0].reason == "saved_de_novo_with_reference_parents_and_quality"
    for missing in flags:
        incomplete = candidate(1, **{**flags, missing: False})
        selection = policy.select_hypotheses([incomplete], provider(pair(incomplete, partner)))
        assert selection.hypotheses[0].second == partner


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("self", "rejected_pair_same_source_allele"),
        ("coordinate_alias", "rejected_pair_same_coordinates"),
        ("chromosome", "rejected_pair_different_chromosomes"),
        ("gene", "rejected_pair_gene_mismatch"),
        ("genotype", "rejected_pair_nonheterozygous_call"),
        ("contradiction", "rejected_pair_observed_contradiction"),
        ("phase", "rejected_pair_unsupported_phase"),
    ],
)
def test_invalid_saved_pairs_have_audited_reasons_and_do_not_exclude_anchor(
    change, expected_reason
):
    anchor, partner = candidate(1), candidate(2)
    saved = pair(anchor, partner)
    if change == "self":
        saved = pair(anchor, anchor)
    elif change == "coordinate_alias":
        saved = pair(anchor, replace(partner, allele=policy.Allele("SYNTHETIC", 101, "a", "t")))
    elif change == "chromosome":
        saved = pair(
            anchor, replace(partner, allele=replace(partner.allele, chrom="chrSYNTHETIC2"))
        )
    elif change == "gene":
        saved = replace(saved, gene="SYNTHETIC_OTHER_GENE")
    elif change == "genotype":
        saved = pair(anchor, replace(partner, genotype="unknown"))
    elif change == "contradiction":
        saved = replace(saved, observed_contradiction=True)
    else:
        saved = replace(saved, phase_status="confirmed_cis")
    selection = policy.select_hypotheses([anchor], provider(saved))
    assert selection.hypotheses[0].first == anchor
    assert selection.hypotheses[0].second is None
    assert selection.hypotheses[0].reason == "exploratory_singleton_no_eligible_saved_pair"
    assert any(item.reason == expected_reason and item.count == 1 for item in selection.audit)


def test_duplicate_source_and_coordinate_anchors_are_consumed():
    first = candidate(1)
    same_source = replace(first, rank=2)
    coordinate_alias = replace(
        candidate(3), allele=policy.Allele("SYNTHETIC", first.allele.pos, "a", "t")
    )
    final = candidate(4)
    selection = policy.select_hypotheses([first, same_source, coordinate_alias, final], provider())
    assert [row.first for row in selection.hypotheses] == [first, final]
    assert [item.reason for item in selection.audit].count("skipped_consumed_endpoint") == 2


def test_stream_is_lazy_and_rejection_audit_is_aggregated():
    anchor, partner = candidate(1), candidate(2)

    def rows(_):
        for _index in range(2000):
            yield pair(anchor, anchor, "0.9")
        yield pair(anchor, partner, "0.8")
        pytest.fail("the provider tail must not be read after the first eligible pair")

    selection = policy.select_hypotheses([anchor], rows)
    assert selection.hypotheses[0].second == partner
    assert len(selection.audit) == 2
    assert selection.audit[0].count == 2000


def test_out_of_order_examined_stream_refuses_instead_of_silently_sorting():
    anchor, partner = candidate(1), candidate(2)

    def rows(_):
        yield pair(anchor, anchor, "0.2")
        yield pair(anchor, partner, "0.9")

    with pytest.raises(policy.PolicyRefusal, match="^pair_stream_not_ordered$"):
        policy.select_hypotheses([anchor], rows)


def test_missing_and_reference_calls_are_audited_without_becoming_hypotheses():
    anchors = [candidate(1, genotype="reference"), candidate(2, genotype="unknown"), candidate(3)]
    selection = policy.select_hypotheses(anchors, provider())
    assert [row.first for row in selection.hypotheses] == [anchors[-1]]
    assert [row.reason for row in selection.audit[:2]] == [
        "unavailable_reference_genotype",
        "unavailable_unknown_genotype",
    ]


def test_fixed_anchor_and_output_limits_and_order():
    anchors = [candidate(rank) for rank in range(1, 102)]
    assert len(policy.select_hypotheses(anchors[:100], provider()).hypotheses) == 10
    assert len(policy.select_hypotheses(anchors[:100], provider(), limit=3).hypotheses) == 3
    with pytest.raises(policy.PolicyRefusal, match="^anchor_window_exceeded$"):
        policy.select_hypotheses(anchors, provider())
    with pytest.raises(policy.PolicyRefusal, match="^anchors_not_in_frozen_rank_order$"):
        policy.select_hypotheses(list(reversed(anchors[:2])), provider())
    with pytest.raises(policy.PolicyRefusal, match="^invalid_hypothesis_limit$"):
        policy.select_hypotheses(anchors[:2], provider(), limit=11)


def test_epcr_fixed_exact_mass_and_residual_are_independent_of_decimal_context():
    with localcontext() as context:
        context.prec = 2
        values = [policy.rank_epcr(rank) for rank in range(1, 11)]
        residual = policy.residual_mass(10)
    assert values[:3] == [Decimal(".10"), Decimal(".08"), Decimal(".064")]
    assert values[-1] == Decimal(".0134217728")
    assert sum(values) == Decimal(".4463129088")
    assert residual == Decimal(".5536870912")
    assert sum(values) + residual == 1
    assert policy.residual_mass(0) == 1
    assert policy.residual_mass(1) == Decimal(".90")
    assert all(earlier > later > 0 for earlier, later in zip(values, values[1:], strict=False))


def test_csv_exact_columns_primary_rows_source_coordinates_and_existing_validator():
    first, second, third = [candidate(rank) for rank in (11, 12, 13)]
    selection = policy.select_hypotheses([first, second, third], provider(pair(first, third)))
    raw = policy.render_csv(selection, "SYNTHETIC_SUBMISSION_LABEL")
    validator.validate_contents(raw, "SYNTHETIC_SUBMISSION_LABEL", validator.ORDER_POLICY)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    assert tuple(rows[0]) == validator.HEADERS == policy.CSV_HEADERS
    assert len(rows) == 3 and all(len(row) == 12 for row in rows)
    assert rows[1][1:5] == ["chrSYNTHETIC", "111", "A", "T"]
    assert rows[1][5:9] == ["chrSYNTHETIC", "113", "A", "T"]
    assert rows[2][5:9] == ["", "", "", ""]
    assert [Decimal(row[9]) for row in rows[1:]] == [Decimal(".10"), Decimal(".08")]
    assert all(row[10] == "primary" for row in rows[1:])
    assert "saved phase=unknown" in rows[1][11]
    assert "uncalibrated" in rows[1][11] and "correlated contexts, no vote" in rows[1][11]


def test_empty_selection_cannot_render_a_submission():
    with pytest.raises(policy.PolicyRefusal, match="^invalid_submission_row_count$"):
        policy.render_csv(policy.select_hypotheses([], provider()), "SYNTHETIC_SUBMISSION_LABEL")
