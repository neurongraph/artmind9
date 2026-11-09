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


def test_aliases_strip_and_casefold_dedupe(plan_dir):
    (plan_dir / "j.yaml").write_text('narrator:\n  entity_class: PERSON\n  name: Ada\n  aliases: [" Ada ", "ada", "A."]\n')
    assert domain_config.narrator("j")["aliases"] == ["Ada", "A."]


@pytest.mark.parametrize("aliases", ['[" "]', "[1]", "x"])
def test_bad_aliases_raise(plan_dir, aliases):
    (plan_dir / "j.yaml").write_text(f"narrator:\n  entity_class: PERSON\n  name: Ada\n  aliases: {aliases}\n")
    with pytest.raises(domain_config.DomainConfigError):
        domain_config.narrator("j")


def test_empty_aliases_ok(plan_dir):
    (plan_dir / "j.yaml").write_text("narrator:\n  entity_class: PERSON\n  name: Ada\n  aliases: []\n")
    assert domain_config.narrator("j")["aliases"] == []


def test_class_name_normalised_and_rejected(plan_dir):
    (plan_dir / "j.yaml").write_text("narrator:\n  entity_class: vip person\n  name: Ada\n")
    assert domain_config.narrator("j")["entity_class"] == "VIP_PERSON"
    (plan_dir / "j.yaml").write_text('narrator:\n  entity_class: "123"\n  name: Ada\n')
    with pytest.raises(domain_config.DomainConfigError):
        domain_config.narrator("j")


def test_empty_and_non_mapping_yaml_give_none(plan_dir):
    (plan_dir / "j.yaml").write_text("")
    assert domain_config.narrator("j") is None
    (plan_dir / "j.yaml").write_text("- x\n")
    assert domain_config.narrator("j") is None


def test_nearest_parent_and_own_file_win(plan_dir):
    (plan_dir / "a.yaml").write_text("narrator:\n  entity_class: PERSON\n  name: Top\n")
    (plan_dir / "a.b.yaml").write_text("narrator:\n  entity_class: PERSON\n  name: Mid\n")
    assert domain_config.narrator("a.b.c")["name"] == "Mid"
    assert domain_config.narrator("a")["name"] == "Top"
    assert domain_config.narrator("a.b")["name"] == "Mid"


def test_config_file_rejects_bad_name(plan_dir):
    assert domain_config.config_file("../x") is None


@pytest.mark.parametrize("name", ["", ".a", "a..b", "a/b"])
def test_is_domain_name_rejects(name):
    assert not domain_config.is_domain_name(name)
