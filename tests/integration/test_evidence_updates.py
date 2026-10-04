"""Exact duplicate evidence writes retain scientific and transactional semantics."""

import json

import duckdb
import pytest

from rare_disease_agent.reporting.provenance import EvidenceProvenance
from rare_disease_agent.storage.evidence import EvidenceStore
from rare_disease_agent.tools.inheritance.schemas import InheritanceEvidence
from rare_disease_agent.tools.phenotype.schemas import PhenotypeScore

RUN = "synthetic-evidence-updates"


def provenance(*, version="v1", label="initial"):
    return EvidenceProvenance(
        tool_version="synthetic",
        data_version=version,
        method="synthetic",
        parameters={"label": label},
        git_commit="synthetic",
        git_dirty=False,
        run_id=RUN,
    )


def phenotype(
    *, gene="SYN_A", score=0.5, associations=1, label="initial", terms=None, version="v1"
):
    return PhenotypeScore(
        gene=gene,
        score=score,
        matched_terms=terms or [],
        unmatched_patient_terms=[],
        data_version=version,
        association_count=associations,
        provenance=provenance(label=label, version=version),
    )


def inheritance(*, variant="synthetic-a", fit=0.5, label="initial", warnings=None):
    return InheritanceEvidence(
        variant_id=variant,
        gene="SYN_A",
        model="autosomal_recessive",
        fit=fit,
        proband_genotype="0/1",
        mother_genotype=None,
        father_genotype=None,
        quality_checks_passed=True,
        evidence=["synthetic evidence"],
        warnings=warnings or [],
        provenance=provenance(label=label),
    )


class AffectedRows:
    """Observe DuckDB's actual INSERT row count, which EvidenceStore does not consume."""

    def __init__(self, connection):
        self.connection = connection
        self.insert_counts = []

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, query, *args, **kwargs):
        result = self.connection.execute(query, *args, **kwargs)
        if " ".join(query.split()).startswith(
            "INSERT INTO evidence SELECT * FROM incoming_evidence"
        ):
            self.insert_counts.append(result.fetchone()[0])
        return result


@pytest.fixture
def storage():
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='64MB'")
        connection.execute("SET threads=1")
        connection.execute("SET temp_directory=''")
        observer = AffectedRows(connection)
        yield connection, EvidenceStore(observer, RUN), observer.insert_counts


def rows(connection):
    return connection.execute(
        "SELECT * FROM evidence ORDER BY run_id,variant_id,gene,method,data_version"
    ).fetchall()


@pytest.mark.parametrize(
    "case", ["stronger", "weaker", "identical", "known", "payload", "provenance"]
)
def test_only_identical_admitted_updates_are_skipped(storage, case):
    connection, store, affected = storage
    initial = phenotype(associations=0 if case == "known" else 1)
    incoming = {
        "stronger": phenotype(score=0.9),
        "weaker": phenotype(score=0.2, label="weaker must not replace"),
        "identical": phenotype(),
        "known": phenotype(associations=1),
        "payload": phenotype(terms=["HP:0000001"]),
        "provenance": phenotype(label="different provenance"),
    }[case]
    assert store.write([initial]) == 1
    assert store.write([incoming, incoming], batch_size=1) == 2
    expected = initial if case == "weaker" else incoming
    assert rows(connection) == [
        (
            RUN,
            "",
            "SYN_A",
            "resnik_bma",
            "v1",
            expected.score,
            expected.association_count > 0,
            expected.model_dump_json(),
        )
    ]
    assert affected == [1, 0 if case in {"identical", "weaker"} else 1, 0]


@pytest.mark.parametrize("batch_size", [1, 256])
def test_duplicate_streams_keep_raw_counts_and_last_highest_payload_on_repeated_writes(
    storage, batch_size
):
    connection, store, _ = storage
    values = [
        inheritance(fit=0.2),
        inheritance(fit=0.9, label="first high"),
        inheritance(fit=0.4, label="lower"),
        inheritance(fit=0.9, label="first high"),
        inheritance(fit=0.9, label="last equal", warnings=["synthetic uncertainty"]),
    ]
    values.append(values[-1])
    for _ in range(3):
        assert store.write(iter(values), batch_size=batch_size) == len(values)
        assert rows(connection) == [
            (
                RUN,
                "synthetic-a",
                "SYN_A",
                "autosomal_recessive",
                "v1",
                0.9,
                True,
                values[-1].model_dump_json(),
            )
        ]
    assert store.max_batch_observed == min(batch_size, len(values))


@pytest.mark.parametrize("previous_known", [False, None])
def test_known_difference_updates_independently_of_tied_score_and_identical_payload(
    storage, previous_known
):
    connection, store, affected = storage
    item = inheritance()
    store.write([item])
    # Isolate this stored column; the incoming evidence remains a typed record.
    connection.execute("UPDATE evidence SET known=?", [previous_known])
    assert store.write([item]) == 1
    assert affected == [1, 1]
    assert rows(connection)[0][-2:] == (True, item.model_dump_json())


@pytest.mark.parametrize("previous_payload", ["different-json-bytes", "null"])
def test_tied_payload_comparison_preserves_exact_bytes_and_repairs_null(storage, previous_payload):
    connection, store, affected = storage
    item = inheritance()
    store.write([item])
    payload = None
    if previous_payload == "different-json-bytes":
        payload = json.dumps(json.loads(item.model_dump_json()), sort_keys=True, indent=2)
        assert payload != item.model_dump_json()
        assert json.loads(payload) == json.loads(item.model_dump_json())
    connection.execute("UPDATE evidence SET payload=?", [payload])
    assert store.write([item]) == 1
    assert affected == [1, 1]
    assert rows(connection)[0][-1] == item.model_dump_json()


def test_null_score_keeps_existing_unknown_admission_rule(storage):
    connection, store, affected = storage
    store.write([inheritance()])
    connection.execute("UPDATE evidence SET score=NULL,known=NULL,payload=NULL")
    before = rows(connection)
    assert store.write([inheritance(fit=0.9, label="different provenance")]) == 1
    assert affected == [1, 0]
    assert rows(connection) == before


def test_later_generator_failure_restores_existing_evidence_and_pair_rows(storage):
    connection, store, _ = storage
    store.write([inheritance(fit=0.2)])
    before = rows(connection)
    connection.execute(
        "CREATE TABLE compound_pairs (run_id VARCHAR, variant_a VARCHAR, "
        "variant_b VARCHAR, payload VARCHAR, PRIMARY KEY(run_id,variant_a,variant_b))"
    )
    connection.execute(
        "INSERT INTO compound_pairs VALUES (?,?,?,?)",
        [RUN, "synthetic-a", "synthetic-b", '{"synthetic":"committed"}'],
    )
    pair_query = "SELECT * FROM compound_pairs ORDER BY run_id,variant_a,variant_b"
    before_pairs = connection.execute(pair_query).fetchall()
    failure = InterruptedError("synthetic generator interruption")

    def interrupted():
        connection.execute(
            "INSERT OR REPLACE INTO compound_pairs VALUES (?,?,?,?)",
            [RUN, "synthetic-a", "synthetic-b", '{"synthetic":"replacement"}'],
        )
        connection.execute(
            "INSERT OR REPLACE INTO compound_pairs VALUES (?,?,?,?)",
            [RUN, "synthetic-a", "synthetic-c", '{"synthetic":"new"}'],
        )
        yield inheritance(fit=0.9)
        yield inheritance(variant="synthetic-b")
        yield inheritance(fit=0.9)
        assert len(rows(connection)) == 2
        assert len(connection.execute(pair_query).fetchall()) == 2
        raise failure

    with pytest.raises(InterruptedError) as caught:
        store.write(interrupted(), batch_size=1)
    assert caught.value is failure
    assert rows(connection) == before
    assert connection.execute(pair_query).fetchall() == before_pairs
    assert store.write([inheritance(fit=0.7)]) == 1


@pytest.mark.parametrize(
    "batch_size,message", [(2, "Mixed evidence data versions"), (1, "Stale evidence data version")]
)
def test_version_rejection_preserves_rows_before_pending_update(storage, batch_size, message):
    connection, store, _ = storage
    store.write([phenotype(score=0.2)])
    before = rows(connection)
    with pytest.raises(ValueError, match=message):
        store.write(
            [phenotype(score=0.9), phenotype(gene="SYN_B", version="v2")], batch_size=batch_size
        )
    assert rows(connection) == before
