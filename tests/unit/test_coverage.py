import pytest

from rare_disease_agent.workflows.coverage import assess_contig_coverage


def test_reference_coverage_accounts_for_every_record_without_mapping_alternates():
    lengths = {"1": 100, "X": 80, "MT": 20, "ALT": 30, "2": 101, "3": 90}
    stats = [
        "1\t100\t10",
        "X\t80\t3",
        "MT\t20\t2",
        "ALT\t30\t4",
        "2\t100\t5",
        "MISSING\t30\t6",
        "3\t90\t7",
    ]
    result = assess_contig_coverage(stats, lengths, set(lengths) - {"3"})
    counts = result["record_counts"]
    assert counts == {
        "autosomal_reference_and_cache_available": 10,
        "sex_chromosome_context_pending": 3,
        "mitochondrial_model_pending": 2,
        "nonstandard_context_pending": 4,
        "annotation_cache_missing": 7,
        "reference_missing": 6,
        "reference_length_mismatch": 5,
    }
    assert result["indexed_records"] == sum(counts.values()) == 37
    assert result["indexed_contigs"] == 7
    assert result["sequence_validation_performed"] is False


@pytest.mark.parametrize("stats", [[], ["1\t100\t1"] * 2, ["1\t.\t1"], ["1\t100\t-1"], ["1\t100"]])
def test_ambiguous_index_statistics_fail_closed(stats):
    with pytest.raises(ValueError):
        assess_contig_coverage(stats, {"1": 100}, {"1"})


def test_sequential_fallback_keeps_original_and_index_unchanged(tmp_path):
    from rare_disease_agent.workflows.coverage import scan_vcf_contig_statistics

    source = tmp_path / "synthetic.vcf"
    index = tmp_path / "synthetic.vcf.tbi"
    index.write_bytes(b"unchanged synthetic index placeholder")
    source.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=100>\n##contig=<ID=X,length=80>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "1\t2\t.\tA\tG\t50\tPASS\t.\n1\t3\t.\tA\tC\t50\tPASS\t.\n"
        "X\t5\t.\tA\tG\t50\tPASS\t.\n"
    )
    before = source.read_bytes(), index.read_bytes()
    stats = scan_vcf_contig_statistics(source)
    assert stats == ["1\t100\t2", "X\t80\t1"]
    result = assess_contig_coverage(
        stats, {"1": 100, "X": 80}, {"1", "X"}, statistics_source="sequential_vcf_scan"
    )
    assert result["indexed_records"] == 3
    assert result["evidence"].startswith("sequential_vcf_scan")
    assert (source.read_bytes(), index.read_bytes()) == before
    source.write_text(source.read_text().replace("ID=X", "ID=Y"))
    with pytest.raises(ValueError, match="context"):
        scan_vcf_contig_statistics(source)


def test_only_unambiguous_standard_aliases_are_proposed():
    from rare_disease_agent.workflows.coverage import standard_reference_aliases

    plan = standard_reference_aliases(["M\t100\t5", "ALT\t100\t8"], {"MT": 100}, {"MT"})
    assert plan["proposed_aliases"] == {"M": "MT"}
    assert plan["affected_records"] == 5
    assert plan["renaming_performed"] is False
    assert not standard_reference_aliases(["M\t99\t5"], {"MT": 100}, {"MT"})["proposed_aliases"]
    assert not standard_reference_aliases(["M\t100\t5"], {"MT": 100}, set())["proposed_aliases"]
    with pytest.raises(ValueError, match="collide"):
        standard_reference_aliases(["M\t100\t5", "MT\t100\t6"], {"MT": 100}, {"MT"})


def test_publisher_synonyms_are_transitive_and_all_records_stay_accounted():
    from rare_disease_agent.workflows.coverage import synonym_reference_plan

    plan = synonym_reference_plan(
        ["1\t100\t3", "alternate_name\t40\t5", "unavailable\t50\t7"],
        {"1": 100, "accession.1": 40},
        {"1", "accession.1"},
        ["alternate_name\tintermediate", "intermediate\taccession.1"],
    )
    assert plan["records"] == 15
    assert plan["record_counts"]["exact_reference_available"] == 3
    assert plan["record_counts"]["synonym_reference_available"] == 5
    assert plan["record_counts"]["reference_missing"] == 7
    assert plan["contig_plan"][1]["target"] == "accession.1"
    assert plan["renaming_performed"] is False


def test_synonyms_never_choose_between_references_or_merge_source_records():
    from rare_disease_agent.workflows.coverage import synonym_reference_plan

    ambiguous = synonym_reference_plan(
        ["alias\t40\t5"],
        {"first": 40, "second": 40},
        {"first", "second"},
        ["alias\tfirst", "first\tsecond"],
    )
    assert ambiguous["record_counts"]["ambiguous_reference"] == 5
    wrong = synonym_reference_plan(["alias\t39\t5"], {"first": 40}, {"first"}, ["alias\tfirst"])
    assert wrong["record_counts"]["reference_length_mismatch"] == 5
    with pytest.raises(ValueError, match="collide"):
        synonym_reference_plan(
            ["alias\t40\t5", "first\t40\t2"], {"first": 40}, {"first"}, ["alias\tfirst"]
        )
    with pytest.raises(ValueError, match="schema"):
        synonym_reference_plan(["alias\t40\t5"], {"first": 40}, {"first"}, ["alias first"])


@pytest.mark.parametrize("cache_available", [False, True], ids=["cache-missing", "cache-present"])
@pytest.mark.parametrize("reverse", [False, True], ids=["alias-first", "exact-first"])
@pytest.mark.parametrize(
    ("synonyms", "consider_name_prefixes"),
    [
        (["alias\tfirst"], False),
        (["alias\tintermediate", "intermediate\tfirst"], False),
        (["chralias\tfirst"], True),
    ],
    ids=["direct", "transitive", "explicit-prefix"],
)
def test_synonym_collisions_are_rejected_independently_of_cache(
    cache_available, reverse, synonyms, consider_name_prefixes
):
    from rare_disease_agent.workflows.coverage import synonym_reference_plan

    statistics = ["alias\t40\t5", "first\t40\t2"]
    if reverse:
        statistics.reverse()
    with pytest.raises(
        ValueError, match="^Source contigs collide after synonym mapping; reconcile explicitly$"
    ):
        synonym_reference_plan(
            statistics,
            {"first": 40},
            {"first"} if cache_available else set(),
            synonyms,
            consider_name_prefixes=consider_name_prefixes,
        )


@pytest.mark.parametrize("cache_available", [False, True], ids=["cache-missing", "cache-present"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_synonym_noncolliding_classifications_remain_distinct(cache_available, reverse):
    from rare_disease_agent.workflows.coverage import synonym_reference_plan

    statistics = [
        "first\t40\t2",
        "wrong_length\t39\t5",
        "ambiguous\t80\t7",
        "missing\t40\t11",
        "alias\t60\t13",
    ]
    if reverse:
        statistics.reverse()
    plan = synonym_reference_plan(
        statistics,
        {"first": 40, "left": 80, "right": 90, "unique": 60},
        {"first", "unique"} if cache_available else set(),
        ["wrong_length\tfirst", "ambiguous\tleft", "left\tright", "alias\tunique"],
    )
    assert plan["records"] == 38
    assert plan["contigs"] == 5
    assert plan["record_counts"] == {
        "exact_reference_available": 2 if cache_available else 0,
        "synonym_reference_available": 13 if cache_available else 0,
        "reference_missing": 11,
        "reference_length_mismatch": 5,
        "ambiguous_reference": 7,
        "annotation_cache_missing": 0 if cache_available else 15,
    }
    assert [row["source"] for row in plan["contig_plan"]] == [
        line.split("\t")[0] for line in statistics
    ]
    assert {row["source"]: row["target"] for row in plan["contig_plan"]} == {
        "first": "first",
        "wrong_length": "first",
        "ambiguous": None,
        "missing": None,
        "alias": "unique",
    }
    assert plan["renaming_performed"] is False
    assert plan["sequence_equivalence_verified"] is False


def test_prefix_candidates_remain_a_nonmutating_explicit_plan():
    from rare_disease_agent.workflows.coverage import synonym_reference_plan

    stats = ["Un_SYNTHv1\t40\t5", "M\t100\t2"]
    refs = {"SYNTH.1": 40, "MT": 100}
    pairs = ["SYNTH.1\tchrUn_SYNTHv1"]
    assert (
        synonym_reference_plan(stats, refs, set(refs), pairs)["record_counts"]["reference_missing"]
        == 7
    )
    plan = synonym_reference_plan(stats, refs, set(refs), pairs, consider_name_prefixes=True)
    assert plan["record_counts"]["synonym_reference_available"] == 7
    assert plan["name_prefixes_considered"] is True
    assert plan["renaming_performed"] is False
    assert plan["sequence_equivalence_verified"] is False
