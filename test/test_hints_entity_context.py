import json
from unittest.mock import patch

from click.testing import CliRunner

from artmind import hints
from artmind.cli import cli


def _row(**kw):
    base = {"entityData": {"_id": "e1", "name": "X"}, "chunks_total": 5, "chunks": [],
            "source_documents_total": 3, "source_documents": [], "connections_total": 3}
    return {**base, **kw}


def test_thin_with_chunk_ids():
    row = _row(chunks_total=1, chunks=[{"id": "c1"}])
    assert hints.entity_context_thin(row, ["bank"]) == (
        "artmind query chunks --shape brief --idList c1 --expand 1 --compact"
    )


def test_thin_brief_chunk_rows_use_chunk_id():
    row = _row(chunks_total=1, chunks=[{"chunk_id": "c9"}])
    assert hints.entity_context_thin(row, ["bank"]).endswith("--idList c9 --expand 1 --compact")


def test_thin_without_chunks_names_first_document():
    row = _row(chunks_total=0, source_documents=[{"id": "d1", "name": "policy.md"}])
    assert hints.entity_context_thin(row, ["bank"]) == (
        'artmind query graph pattern10 --domain bank --documentName policy.md --compact'
    )


def test_thin_silent_when_enough_chunks_or_no_documents():
    assert hints.entity_context_thin(_row(chunks_total=2), ["bank"]) is None
    assert hints.entity_context_thin(_row(chunks_total=0, source_documents_total=0), ["bank"]) is None


def test_cut_names_pattern8_all():
    row = _row(connections_total=40)
    assert hints.entity_context_cut(row, ["bank"], 25) == (
        "artmind query graph pattern8 --domain bank --entityId e1 --entityClass <CLASS> --via mentions --all --shape brief --compact"
    )
    assert hints.entity_context_cut(row, ["bank"], 40) is None


def _invoke(row, *extra):
    payload = {"rows": [row]}
    with patch("artmind.cli.graph_query.entity_context", return_value=payload):
        res = CliRunner().invoke(cli, ["query", "entity-context", "--domain", "bank",
                                       "--entityId", "e1", "--compact", *extra])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_cli_thin_and_cut_joined():
    out = _invoke(_row(chunks_total=1, chunks=[{"id": "c1"}], connections_total=40))
    assert out["next"] == (
        "artmind query graph pattern8 --domain bank --entityId e1 --entityClass <CLASS> --via mentions --all --shape brief --compact; "
        "artmind query chunks --shape brief --idList c1 --expand 1 --compact"
    )


def test_cli_puts_result_signposts_before_the_generic_shape_hint():
    payload = {"rows": [_row(chunks_total=1, chunks=[{"id": "c1"}])], "next": "rerun with --shape full"}
    with patch("artmind.cli.graph_query.entity_context", return_value=payload):
        res = CliRunner().invoke(cli, ["query", "entity-context", "--domain", "bank",
                                       "--entityId", "e1", "--compact"])
    assert json.loads(res.stdout)["next"] == (
        "artmind query chunks --shape brief --idList c1 --expand 1 --compact; rerun with --shape full"
    )


def test_cli_full_row_has_no_next():
    assert _invoke(_row())["next"] is None
