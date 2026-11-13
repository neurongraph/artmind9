"""`orient` lists `general` when it holds graph data or a structured table, not otherwise."""
import pytest

from artmind import domain_guard, orient

pytestmark = pytest.mark.real_domain_guard


def _stub(monkeypatch, tables, graph):
    monkeypatch.setattr(domain_guard, "_schema_domains", lambda: {"general", "journal"})
    monkeypatch.setattr(domain_guard, "_table_domains", lambda: set(tables))
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: set(graph))


def test_general_with_only_a_table_is_listed(monkeypatch):
    _stub(monkeypatch, tables={"general"}, graph=set())
    assert "general" in orient._known_domains()


def test_general_with_graph_data_is_listed(monkeypatch):
    _stub(monkeypatch, tables=set(), graph={"general"})
    assert "general" in orient._known_domains()


def test_general_with_nothing_is_hidden(monkeypatch):
    _stub(monkeypatch, tables=set(), graph=set())
    assert "general" not in orient._known_domains()
