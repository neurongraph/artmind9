"""The planner-free per-domain config reader (spec 2026-11-09 §5.3)."""
import pytest

from artmind import domain_config


@pytest.fixture
def plan_dir(tmp_path, monkeypatch):
    import paths

    d = tmp_path / "plan"
    d.mkdir()
    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", d)
    return d


def test_is_domain_name():
    assert domain_config.is_domain_name("bank.policy")
    assert not domain_config.is_domain_name("../x")
    assert not domain_config.is_domain_name("a\n")
    assert not domain_config.is_domain_name(None)


def test_narrator_from_own_file(plan_dir):
    (plan_dir / "journal.yaml").write_text("narrator:\n  entity_class: person\n  name: Ada\n  aliases: [A.]\n")
    n = domain_config.narrator("journal")
    assert n == {"entity_class": "PERSON", "name": "Ada", "aliases": ["A."]}


def test_narrator_inherited_from_parent_file(plan_dir):
    (plan_dir / "journal.yaml").write_text("narrator:\n  entity_class: PERSON\n  name: Ada\n")
    assert domain_config.narrator("journal.work")["name"] == "Ada"


def test_no_file_or_no_narrator_is_none(plan_dir):
    assert domain_config.narrator("journal") is None
    (plan_dir / "journal.yaml").write_text("calendar: []\n")
    assert domain_config.narrator("journal") is None


def test_broken_file_raises_config_error(plan_dir):
    (plan_dir / "journal.yaml").write_text("narrator: [x\n")
    with pytest.raises(domain_config.DomainConfigError):
        domain_config.narrator("journal")
    (plan_dir / "journal.yaml").write_text("narrator: {name: Ada}\n")
    with pytest.raises(domain_config.DomainConfigError):
        domain_config.narrator("journal")
