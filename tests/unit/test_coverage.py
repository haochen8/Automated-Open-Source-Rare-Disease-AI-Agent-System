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
