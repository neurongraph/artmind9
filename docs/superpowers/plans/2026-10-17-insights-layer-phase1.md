# Insights layer — phase 1 (core, first detectors, graph health) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `artmind insights refresh|list|show|profile|runs`. It profiles a domain's graph against its schema, then runs the trend, dormancy, community, broker and graph-health detectors, and writes a diffed snapshot into the vault.

**Architecture:** A new package `artmind/insights/`.
- Named read-only Cypher **projections** pull the rows the run needs, through `graph_query.read_session`.
- A **profile** measures the graph's shape (class sizes, effective kind, property vocabularies, relationship triples, conflicts).
- **Detectors** are pure Python over those rows, plus networkx for graph algorithms. They return `Finding`s.
- The **runner** trims and diffs the findings against the previous snapshot and writes JSON atomically under `paths.INSIGHTS_DIR`.
- The CLI is thin and imports the package lazily.

**Tech Stack:** Python ≥3.14, Click (rich-click), neo4j driver, networkx ≥3.4 (pure Python; no numpy/scipy), PyYAML, pytest.

Spec: `docs/superpowers/specs/2026-10-17-insights-layer-design.md`. Phase 1 = spec §15 item 1.

---

## Conventions for every task

- Run tests with `uv run --group dev pytest <files> -q`. Target the task's own files. The full suite stalls on this Mac (vault-radar hook), so run only `test/test_insights_*.py` plus any file the task touches.
- Tests are hermetic: no Neo4j, no network. Detectors are tested on hand-built rows. Projections are tested by recording the Cypher and params sent.
- **Synthetic data only.** Never copy names or text from a real vault into code, tests or commits.
- Commit after each task with the message shown. End every commit message with:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## File map

| File | Responsibility |
|---|---|
| `paths.py` (modify) | `DOMAIN_INSIGHTS_DIR`, `INSIGHTS_DIR` |
| `artmind/vault.py` (modify) | `VaultLayout.insights_config_dir`, `VaultLayout.insights_dir` |
| `pyproject.toml` (modify) | `networkx>=3.4` core dependency |
| `artmind/insights/__init__.py` | package docstring |
| `artmind/insights/model.py` | `InsightsError`, `Finding`, `PlanEntry`, `Run`, JSON round-trip |
| `artmind/insights/config.py` | load/validate `domains/insights/<domain>.yaml`, `config_hash` |
| `artmind/insights/stats.py` | periods, gaps, Theil–Sen, support weight, rates |
| `artmind/insights/projections.py` | named Cypher projections + memoising `Projector` |
| `artmind/insights/profile.py` | `EntityRow`, `ClassProfile`, `Profile`, `build_profile`, effective kind, declared triples, anchors |
| `artmind/insights/graphs.py` | co-mention graph, Louvain, modularity, weighted degree, period restriction |
| `artmind/insights/detectors/__init__.py` | `Skip`, `Detector`, `RunContext`, `subject()`, `registry()` |
| `artmind/insights/detectors/salience.py` | `salience_trend`, `type_trend` |
| `artmind/insights/detectors/dormancy.py` | `dormancy` |
| `artmind/insights/detectors/communities.py` | `communities` (circles, shares, cohesion) |
| `artmind/insights/detectors/brokers.py` | `brokers` |
| `artmind/insights/detectors/health.py` | `kind_mismatch`, `vocabulary_sprawl`, `relationship_usage`, `open_conflicts` |
| `artmind/insights/diff.py` | match findings to the previous run, mark new/changed/same/gone |
| `artmind/insights/store.py` | snapshot paths, atomic write, latest, list, prune |
| `artmind/insights/runner.py` | the pipeline: `refresh()` |
| `artmind/cli.py` (modify) | `insights` group + `COMMAND_GROUPS` |
| `test/_insights_helpers.py` | `ent`, `year_days`, `make_ctx`, `FakeProjector` |
| `test/test_insights_*.py` | one test file per module |
| `CLAUDE.md`, `justfile` (modify) | docs and recipes |

---

### Task 1: Dependency, paths and vault layout

**Files:**
- Modify: `pyproject.toml` (the `dependencies` list)
- Modify: `paths.py:197-203`
- Modify: `artmind/vault.py` (after the `reviews_dir` property)
- Create: `test/test_insights_layout.py`
- Modify: `test/test_scaffold_run_folder.py` (append one test)
- Modify: `test/test_vault_scaffold.py` (append one test)

- [ ] **Step 1: Write the failing tests**

`test/test_insights_layout.py`:

```python
"""Where insights live (spec 2026-10-17 §4.3, §9.1)."""

import paths
from artmind.vault import VaultLayout


def test_vault_layout_insights_dirs(tmp_path):
    layout = VaultLayout(tmp_path)
    assert layout.insights_config_dir == tmp_path / ".artmind" / "domains" / "insights"
    assert layout.insights_dir == tmp_path / ".artmind" / "insights"


def test_paths_insights_dirs_follow_artmind_home():
    assert paths.DOMAIN_INSIGHTS_DIR == paths.ARTMIND_HOME / "domains" / "insights"
    assert paths.INSIGHTS_DIR == paths.ARTMIND_HOME / "insights"
    # Tuning lives beside views, never inside schemas/: schema_set_hash() hashes schemas/*.yaml.
    assert paths.DOMAIN_INSIGHTS_DIR.parent == paths.DOMAIN_SCHEMAS_DIR.parent
    assert paths.DOMAIN_INSIGHTS_DIR != paths.DOMAIN_SCHEMAS_DIR


def test_networkx_algorithms_used_by_insights_are_available():
    import networkx as nx
    from networkx.algorithms import community

    g = nx.karate_club_graph()
    assert community.louvain_communities(g, seed=7)
    assert nx.betweenness_centrality(g)
```

Append to `test/test_scaffold_run_folder.py` (it reuses `_write` and `_patch_scaffold_dirs`, which are already in that file):

```python
def test_scaffold_run_folder_never_touches_insights(tmp_path, monkeypatch):
    """Insights config and snapshots are user data: setup must leave them alone."""
    import artmind.setup as setup

    home, data = tmp_path / "home", tmp_path / "data"
    _patch_scaffold_dirs(setup, monkeypatch, home, data)
    monkeypatch.setattr(setup, "resolve_vault", lambda: None)
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", tmp_path / "no-such-env-example")
    monkeypatch.setattr(setup, "DOMAIN_META_PATH", home / "domains" / "meta.yaml")
    monkeypatch.setattr(setup, "PACKAGE_META_YAML", tmp_path / "no-such-meta")
    _write(tmp_path / "pkg-schemas" / "demo_schema.yaml", "kind: demo\n")
    monkeypatch.setattr(setup, "PACKAGE_SCHEMAS_DIR", tmp_path / "pkg-schemas")
    monkeypatch.setattr(setup, "validate_all_or_raise", lambda *a, **k: None)
    cfg = home / "domains" / "insights" / "demo.yaml"
    snap = home / "insights" / "demo" / "latest.json"
    _write(cfg, "period: quarter\n")
    _write(snap, "{}\n")

    setup.scaffold_run_folder()

    assert cfg.read_text() == "period: quarter\n"
    assert snap.read_text() == "{}\n"
```

Append to `test/test_vault_scaffold.py` (it reuses `_init_repo`, `_ignored` and `_git` from that file):

```python
def test_insights_files_are_committed_not_ignored(tmp_path):
    _init_repo(tmp_path)
    vault.write_gitignore(tmp_path)
    layout = vault.VaultLayout(tmp_path)
    (layout.insights_config_dir).mkdir(parents=True)
    (layout.insights_config_dir / "demo.yaml").write_text("period: year\n")
    runs = layout.insights_dir / "demo" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-10-17T09-30-12Z.json").write_text("{}\n")

    for rel in (".artmind/domains/insights/demo.yaml", ".artmind/insights/demo/runs/2026-10-17T09-30-12Z.json"):
        assert not _ignored(tmp_path, rel)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run --group dev pytest test/test_insights_layout.py test/test_scaffold_run_folder.py::test_scaffold_run_folder_never_touches_insights test/test_vault_scaffold.py::test_insights_files_are_committed_not_ignored -q`

Expected: FAIL with `AttributeError: ... has no attribute 'insights_config_dir'`, then `ModuleNotFoundError: No module named 'networkx'`.

- [ ] **Step 3: Implement**

In `pyproject.toml`, add to the `dependencies` list (keep it alphabetical):

```toml
    "networkx>=3.4",
```

Then lock and sync (never hand-edit `uv.lock`):

```bash
uv lock && uv sync --group dev
```

In `paths.py`, after the `DOMAIN_VIEWS_DIR = ...` line add:

```python
# Insights tuning (spec 2026-10-17 §4.3): one <domain>.yaml per domain. User
# data like views -- never seeded -- and outside schemas/ so tuning a detector
# never changes projection.schema_set_hash().
DOMAIN_INSIGHTS_DIR = ARTMIND_HOME / "domains" / "insights"
```

After the `REVIEWS_DIR = ...` line add:

```python
# Insights snapshots (spec 2026-10-17 §9.1): <vault>/.artmind/insights/<domain>/
# in a vault, committed with its private repo; private corpus content.
INSIGHTS_DIR = ARTMIND_HOME / "insights"
```

In `artmind/vault.py`, directly after the `reviews_dir` property add:

```python
    @property
    def insights_config_dir(self) -> Path:
        """Insights tuning, one `<domain>.yaml` per domain (spec 2026-10-17 §4.3).
        User-authored and committed; never seeded; outside `schemas/` so tuning
        never changes schema_set_hash()."""
        return self.domains_dir / "insights"

    @property
    def insights_dir(self) -> Path:
        """Insights snapshots (spec 2026-10-17 §9.1): committed with the vault,
        private corpus content like reviews."""
        return self.artmind_dir / "insights"
```

- [ ] **Step 4: Run the tests to see them pass**

Run the same command as Step 2. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock paths.py artmind/vault.py test/test_insights_layout.py test/test_scaffold_run_folder.py test/test_vault_scaffold.py
git commit -m "feat(insights): networkx dependency and insights config/snapshot locations"
```

---

### Task 2: Data model

**Files:**
- Create: `artmind/insights/__init__.py`
- Create: `artmind/insights/model.py`
- Test: `test/test_insights_model.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from artmind.insights.model import FORMAT, Finding, InsightsError, PlanEntry, Run


def _finding(**over):
    base = dict(key="dormancy:PERSON:p1", detector="dormancy", kind="corpus", score=0.5, title="Quiet: Asha")
    return Finding(**{**base, **over})


def test_finding_defaults_and_round_trip():
    f = _finding(stats={"headline": 3})
    assert f.diff == "new" and f.subjects == [] and f.action is None and f.narration is None
    assert Finding.from_dict(f.to_dict()) == f


def test_finding_rejects_bad_score_and_kind():
    with pytest.raises(ValueError):
        _finding(score=1.2)
    with pytest.raises(ValueError):
        _finding(kind="other")


def test_finding_from_dict_ignores_unknown_keys():
    d = {**_finding().to_dict(), "future_field": 1}
    assert Finding.from_dict(d) == _finding()


def test_run_round_trip_and_format_guard():
    run = Run(
        domain="demo", created_at="2026-10-17T09:30:12Z", as_of="2026-10-01",
        fingerprint={"entities": 3}, anchors=[], profile={}, circles=[], diff={},
        plan=[PlanEntry(detector="dormancy", status="ran", target="PERSON", findings=1)],
        findings=[_finding()],
    )
    d = run.to_dict()
    assert d["format"] == FORMAT
    assert Run.from_dict(d) == run
    with pytest.raises(InsightsError):
        Run.from_dict({**d, "format": FORMAT + 1})
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_model.py -q`

Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.insights'`.

- [ ] **Step 3: Implement**

`artmind/insights/__init__.py`:

```python
"""insights: unprompted, schema-driven findings over a domain's graph, saved as
diffed snapshots (spec docs/superpowers/specs/2026-10-17-insights-layer-design.md)."""
```

`artmind/insights/model.py`:

```python
"""Insights data model: findings, plan entries and runs (spec 2026-10-17 §3, §9.1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

FORMAT = 1
KINDS = ("corpus", "health")


class InsightsError(Exception):
    """A user-facing insights failure: bad config, unreachable graph, unreadable snapshot."""


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class Finding:
    key: str
    detector: str
    kind: str
    score: float
    title: str
    subjects: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    card: dict = field(default_factory=dict)
    action: str | None = None
    narration: str | None = None
    diff: str = "new"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.key}: kind {self.kind!r} is not one of {KINDS}")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"{self.key}: score {self.score} is outside [0, 1]")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        return cls(**_known(cls, data))


@dataclass
class PlanEntry:
    detector: str
    status: str  # ran | skipped | failed
    target: str | None = None
    reason: str | None = None
    findings: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PlanEntry:
        return cls(**_known(cls, data))


@dataclass
class Run:
    domain: str
    created_at: str
    as_of: str | None
    fingerprint: dict
    anchors: list[dict]
    profile: dict
    plan: list[PlanEntry]
    findings: list[Finding]
    circles: list[dict]
    diff: dict

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "domain": self.domain,
            "created_at": self.created_at,
            "as_of": self.as_of,
            "fingerprint": self.fingerprint,
            "anchors": self.anchors,
            "profile": self.profile,
            "plan": [p.to_dict() for p in self.plan],
            "findings": [f.to_dict() for f in self.findings],
            "circles": self.circles,
            "diff": self.diff,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        if data.get("format") != FORMAT:
            raise InsightsError(f"snapshot format {data.get('format')!r} is not {FORMAT}")
        return cls(
            domain=data["domain"],
            created_at=data["created_at"],
            as_of=data.get("as_of"),
            fingerprint=data.get("fingerprint") or {},
            anchors=data.get("anchors") or [],
            profile=data.get("profile") or {},
            plan=[PlanEntry.from_dict(p) for p in data.get("plan") or []],
            findings=[Finding.from_dict(f) for f in data.get("findings") or []],
            circles=data.get("circles") or [],
            diff=data.get("diff") or {},
        )
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_model.py -q`. Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/__init__.py artmind/insights/model.py test/test_insights_model.py
git commit -m "feat(insights): finding, plan entry and run model with JSON round-trip"
```

---

### Task 3: Config

**Files:**
- Create: `artmind/insights/config.py`
- Test: `test/test_insights_config.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

import paths
from artmind.insights import config as cfg_mod
from artmind.insights.config import KNOWN_DETECTORS, load_config, parse_config
from artmind.insights.model import InsightsError


@pytest.fixture
def cfg_root(tmp_path, monkeypatch):
    root = tmp_path / "insights-config"
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", root)
    return root


def test_missing_file_gives_defaults(cfg_root):
    c = load_config("demo")
    assert (c.domain, c.period, c.min_evidence_days, c.min_period_docs) == ("demo", "year", 4, 20)
    assert (c.max_findings, c.keep_runs, c.narrate, c.anchors) == (25, 12, True, ())
    assert cfg_mod.config_hash("demo") == ""


def test_file_values_are_read_and_hashed(cfg_root):
    cfg_root.mkdir(parents=True)
    (cfg_root / "demo.yaml").write_text(
        "period: quarter\nanchors: ['@e1']\ndetectors:\n  dormancy: {silence_multiple: 4}\n  brokers: {enabled: false}\n"
    )
    c = load_config("demo")
    assert c.period == "quarter" and c.anchors == ("@e1",)
    assert c.detector("dormancy") == {"silence_multiple": 4}
    assert c.detector("salience_trend") == {}
    assert c.enabled("dormancy") and not c.enabled("brokers")
    assert len(cfg_mod.config_hash("demo")) == 64


def test_phase_two_blocks_are_kept_verbatim():
    c = parse_config({"outcome": {"class": "EMOTION"}, "open_loops": {"class": "PLAN"}}, "demo")
    assert c.raw == {"outcome": {"class": "EMOTION"}, "open_loops": {"class": "PLAN"}}


@pytest.mark.parametrize(
    "data, message",
    [
        ({"colour": "red"}, "unknown key"),
        ({"domain": "other"}, "domain"),
        ({"period": "week"}, "period"),
        ({"min_evidence_days": 0}, "min_evidence_days"),
        ({"keep_runs": True}, "keep_runs"),
        ({"anchors": "Asha"}, "anchors"),
        ({"detectors": {"dormancy": 3}}, "detectors"),
        ({"detectors": {"dormant": {}}}, "unknown detector"),
        ({"narrate": "yes"}, "narrate"),
    ],
)
def test_invalid_config_is_refused(data, message):
    with pytest.raises(InsightsError, match=message):
        parse_config(data, "demo")


def test_bad_yaml_is_an_insights_error(cfg_root):
    cfg_root.mkdir(parents=True)
    (cfg_root / "demo.yaml").write_text("period: [unclosed\n")
    with pytest.raises(InsightsError, match="not valid YAML"):
        load_config("demo")


def test_known_detectors_cover_the_spec():
    assert {"salience_trend", "type_trend", "dormancy", "communities", "brokers", "kind_mismatch",
            "vocabulary_sprawl", "relationship_usage", "open_conflicts", "outcome_lift"} <= KNOWN_DETECTORS
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_config.py -q`. Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `artmind/insights/config.py`

```python
"""Insights tuning: `<ARTMIND_HOME>/domains/insights/<domain>.yaml` (spec 2026-10-17 §4.3).

Every key is optional. Unknown keys and unknown detector names are errors, so a
typo never silently does nothing. Phase-2 blocks (outcome, transitions,
open_loops) are validated as mappings and kept verbatim in `raw`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from artmind.insights.model import InsightsError

PERIODS = ("year", "quarter", "month")
KNOWN_DETECTORS = frozenset({
    "salience_trend", "type_trend", "dormancy", "communities", "brokers",
    "outcome_lift", "transitions", "open_loops", "same_as_candidates",
    "kind_mismatch", "vocabulary_sprawl", "relationship_usage", "attribution_suspects",
    "open_conflicts", "fragments", "split_signal",
})
_INTS = {  # key: (default, minimum)
    "min_evidence_days": (4, 1),
    "min_period_docs": (20, 1),
    "max_findings": (25, 1),
    "keep_runs": (12, 1),
    "narrate_top": (15, 0),
}
_RAW_BLOCKS = ("outcome", "transitions", "open_loops")
KNOWN_KEYS = frozenset({"domain", "anchors", "period", "narrate", "detectors", *_INTS, *_RAW_BLOCKS})


@dataclass(frozen=True)
class InsightsConfig:
    domain: str
    anchors: tuple[str, ...] = ()
    period: str = "year"
    min_evidence_days: int = 4
    min_period_docs: int = 20
    max_findings: int = 25
    keep_runs: int = 12
    narrate_top: int = 15
    narrate: bool = True
    detectors: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def detector(self, name: str) -> dict:
        settings = dict(self.detectors.get(name) or {})
        settings.pop("enabled", None)
        return settings

    def enabled(self, name: str) -> bool:
        return (self.detectors.get(name) or {}).get("enabled", True) is not False


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_INSIGHTS_DIR)


def config_path(domain: str) -> Path:
    return config_dir() / f"{domain}.yaml"


def config_hash(domain: str) -> str:
    path = config_path(domain)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def parse_config(data: dict | None, domain: str) -> InsightsConfig:
    where = f"insights config for {domain}"
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise InsightsError(f"{where}: must be a mapping")
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise InsightsError(f"{where}: unknown key(s) {sorted(unknown)}")
    if data.get("domain") not in (None, domain):
        raise InsightsError(f"{where}: domain is {data['domain']!r}, expected {domain!r}")
    period = data.get("period", "year")
    if period not in PERIODS:
        raise InsightsError(f"{where}: period must be one of {list(PERIODS)}")
    ints = {}
    for key, (default, minimum) in _INTS.items():
        value = data.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise InsightsError(f"{where}: {key} must be an integer >= {minimum}")
        ints[key] = value
    anchors = data.get("anchors") or []
    if not isinstance(anchors, list) or not all(isinstance(a, str) and a.strip() for a in anchors):
        raise InsightsError(f"{where}: anchors must be a list of names or @<_id> strings")
    detectors = data.get("detectors") or {}
    if not isinstance(detectors, dict) or not all(isinstance(v, dict) for v in detectors.values()):
        raise InsightsError(f"{where}: detectors must map a detector name to a mapping of settings")
    bad = set(detectors) - KNOWN_DETECTORS
    if bad:
        raise InsightsError(f"{where}: unknown detector(s) {sorted(bad)}")
    narrate = data.get("narrate", True)
    if not isinstance(narrate, bool):
        raise InsightsError(f"{where}: narrate must be true or false")
    raw = {}
    for key in _RAW_BLOCKS:
        if key in data:
            if not isinstance(data[key], dict):
                raise InsightsError(f"{where}: {key} must be a mapping")
            raw[key] = data[key]
    return InsightsConfig(
        domain=domain, anchors=tuple(a.strip() for a in anchors), period=period,
        narrate=narrate, detectors=dict(detectors), raw=raw, **ints,
    )


def load_config(domain: str) -> InsightsConfig:
    path = config_path(domain)
    if not path.is_file():
        return parse_config({}, domain)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InsightsError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(data, domain)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_config.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/config.py test/test_insights_config.py
git commit -m "feat(insights): per-domain insights config with strict validation"
```

---

### Task 4: Stats helpers

**Files:**
- Create: `artmind/insights/stats.py`
- Test: `test/test_insights_stats.py`

- [ ] **Step 1: Write the failing test**

```python
import math

import pytest

from artmind.insights import stats


@pytest.mark.parametrize("period, expected", [("year", "2024"), ("quarter", "2024-Q2"), ("month", "2024-05")])
def test_period_of(period, expected):
    assert stats.period_of("2024-05-17", period) == expected


def test_period_of_rejects_unknown_period():
    with pytest.raises(ValueError):
        stats.period_of("2024-05-17", "week")


def test_days_between_and_median_gap():
    assert stats.days_between("2024-01-01", "2024-01-31") == 30
    assert stats.median_gap_days(["2024-01-01", "2024-01-11", "2024-01-21", "2024-03-01"]) == 10.0
    assert stats.median_gap_days(["2024-01-01"]) == 0.0


def test_theil_sen_is_robust_to_one_outlier():
    assert stats.theil_sen_slope([1, 2, 3, 4, 100]) == 1.0
    assert stats.theil_sen_slope([5]) == 0.0


def test_support_weight_and_rate():
    assert stats.support_weight(30) == 1.0
    assert stats.support_weight(120) == 1.0
    assert math.isclose(stats.support_weight(7.5), 0.5)
    assert stats.rate_per_100(5, 50) == 10.0
    assert stats.rate_per_100(5, 0) == 0.0
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_stats.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/stats.py`

```python
"""Small, dependency-free statistics shared by detectors."""

from __future__ import annotations

import math
import statistics
from datetime import date


def period_of(day: str, period: str) -> str:
    """'2024-05-17' -> '2024' | '2024-Q2' | '2024-05'."""
    if period == "year":
        return day[:4]
    if period == "quarter":
        return f"{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}"
    if period == "month":
        return day[:7]
    raise ValueError(f"unknown period {period!r}")


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def median_gap_days(days: list[str] | tuple[str, ...]) -> float:
    """Median gap between consecutive distinct days; 0.0 with fewer than two days."""
    ordered = sorted({date.fromisoformat(d) for d in days})
    if len(ordered) < 2:
        return 0.0
    return float(statistics.median((b - a).days for a, b in zip(ordered, ordered[1:])))


def theil_sen_slope(values: list[float]) -> float:
    """Median of pairwise slopes: a trend that one odd period cannot swing."""
    n = len(values)
    if n < 2:
        return 0.0
    slopes = [(values[j] - values[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return float(statistics.median(slopes))


def support_weight(support: float, full: float = 30) -> float:
    """Down-weights findings resting on little evidence; 1.0 at `full` and above."""
    return min(1.0, math.sqrt(max(support, 0) / full))


def rate_per_100(count: int, docs: int) -> float:
    return 100.0 * count / docs if docs else 0.0
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_stats.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/stats.py test/test_insights_stats.py
git commit -m "feat(insights): period, gap, Theil-Sen and support helpers"
```

---

### Task 5: Projections

**Files:**
- Create: `artmind/insights/projections.py`
- Test: `test/test_insights_projections.py`

- [ ] **Step 1: Write the failing test**

```python
"""Assert the Cypher and params actually SENT (pattern: test_kg_views_runner.py)."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable

from artmind.insights.model import InsightsError
from artmind.insights.projections import PROJECTIONS, Projector
from artmind.text2cypher import validate_domain_scoped, validate_read_only


class Recorder:
    def __init__(self, rows=(), raises=None):
        self.calls = []
        self.rows, self.raises = list(rows), raises

    def factory(self):
        @contextmanager
        def session_cm():
            session = MagicMock()
            session.run.side_effect = self._run
            yield session
        return session_cm

    def _run(self, query, parameters=None, **kwargs):
        self.calls.append((getattr(query, "text", query), parameters, getattr(query, "timeout", None)))
        if self.raises:
            raise self.raises
        result = MagicMock()
        result.__iter__.return_value = iter(self.rows)
        return result


@pytest.mark.parametrize("name", sorted(PROJECTIONS))
def test_every_projection_is_read_only_and_domain_scoped(name):
    validate_read_only(PROJECTIONS[name])
    validate_domain_scoped(PROJECTIONS[name])


def test_projector_sends_domains_params_and_timeout():
    rec = Recorder(rows=[{"_id": "e1"}])
    p = Projector(["demo", "demo.sub"], session_factory=rec.factory())
    assert p.rows("relation_triples", max_targets=10) == [{"_id": "e1"}]
    cypher, params, timeout = rec.calls[0]
    assert cypher == PROJECTIONS["relation_triples"]
    assert params == {"max_targets": 10, "domains": ["demo", "demo.sub"]}
    assert timeout == 60


def test_projector_memoises_per_name_and_params():
    rec = Recorder(rows=[{"x": 1}])
    p = Projector(["demo"], session_factory=rec.factory())
    p.rows("documents")
    p.rows("documents")
    p.rows("relation_triples", max_targets=5)
    assert len(rec.calls) == 2


def test_projector_wraps_driver_errors():
    rec = Recorder(raises=ServiceUnavailable("down"))
    with pytest.raises(InsightsError, match="projection documents failed"):
        Projector(["demo"], session_factory=rec.factory()).rows("documents")


def test_unknown_projection_and_empty_domains():
    with pytest.raises(InsightsError):
        Projector(["demo"], session_factory=Recorder().factory()).rows("nope")
    with pytest.raises(InsightsError):
        Projector([])
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_projections.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/projections.py`

```python
"""Named read-only Cypher projections for the insights pipeline (spec 2026-10-17 §5).

Every statement is `$domains`-scoped, runs in a server-enforced read-only session
under a 60 s timeout, and is memoised per run, so detectors sharing a projection
pay for it once. Detectors never hold a driver themselves.
"""

from __future__ import annotations

import json

from neo4j import Query
from neo4j.exceptions import DriverError, Neo4jError

from artmind.graph_query import read_session
from artmind.insights.model import InsightsError

QUERY_TIMEOUT_S = 60
# Free text or identity by construction: never a vocabulary worth profiling.
SKIP_PROPERTY_KEYS = ("embedding", "embedding_stale", "name", "aliases", "description", "context", "key", "entity_class")

PROJECTIONS: dict[str, str] = {
    # One row per entity: the distinct document valid dates it was observed on.
    "entity_evidence": """
MATCH (e:Entity)-[:AGGREGATES]->(o:Observation)
WHERE e._domain IN $domains
WITH e, collect(DISTINCT o._doc_valid_from) AS days, count(DISTINCT o.doc_id) AS docs
RETURN e._id AS _id, e.name AS name, e.entity_class AS cls, e.type AS type, days AS days, docs AS docs
ORDER BY _id
""",
    "documents": """
MATCH (d:Document)
WHERE d._domain IN $domains
RETURN d.id AS id, d._valid_from AS valid_from, d._valid_time_source AS time_source
ORDER BY id
""",
    "property_stats": """
MATCH (e:Entity)
WHERE e._domain IN $domains
UNWIND [k IN keys(e) WHERE NOT k STARTS WITH '_' AND NOT k IN $skip_keys] AS k
WITH e.entity_class AS cls, k, e[k] AS v
WITH cls, k, CASE WHEN valueType(v) STARTS WITH 'LIST' THEN null ELSE toString(v) END AS sv
WITH cls, k, count(*) AS filled, count(DISTINCT sv) AS distinct_values, collect(DISTINCT sv) AS vals
RETURN cls AS cls, k AS property, filled AS filled, distinct_values AS distinct_values, vals[..200] AS sample
ORDER BY cls, property
""",
    "relation_triples": """
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
WHERE a._domain IN $domains AND b._domain IN $domains
WITH r.rel_type AS rel, a.entity_class AS src, b.entity_class AS dst, count(*) AS n, collect(DISTINCT b._id) AS targets
RETURN rel AS rel, src AS src, dst AS dst, n AS n, targets[..$max_targets] AS targets
ORDER BY n DESC, rel, src, dst
""",
    "open_conflicts": """
MATCH (c:Conflict)-[:CONFLICT_OF]->(e:Entity)
WHERE e._domain IN $domains AND c._domain IN $domains AND c.status = 'open'
RETURN e.entity_class AS cls, c.property AS property, count(*) AS n
ORDER BY n DESC, cls, property
""",
}


class Projector:
    def __init__(self, domains: list[str], session_factory=None):
        if not domains:
            raise InsightsError("no domains to project")
        self.domains = list(domains)
        self._session_factory = session_factory or read_session
        self._memo: dict[tuple[str, str], list[dict]] = {}

    def rows(self, name: str, **params) -> list[dict]:
        if name not in PROJECTIONS:
            raise InsightsError(f"unknown projection {name!r}")
        memo_key = (name, json.dumps(params, sort_keys=True, default=str))
        if memo_key in self._memo:
            return self._memo[memo_key]
        bindings = {**params, "domains": self.domains}
        try:
            with self._session_factory() as session:
                rows = [dict(rec) for rec in session.run(Query(PROJECTIONS[name], timeout=QUERY_TIMEOUT_S), bindings)]
        except (Neo4jError, DriverError) as exc:
            detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            raise InsightsError(f"projection {name} failed: {detail}") from exc
        self._memo[memo_key] = rows
        return rows
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_projections.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/projections.py test/test_insights_projections.py
git commit -m "feat(insights): read-only domain-scoped projections with a memoising projector"
```

---

### Task 6: Shape profile and anchors

**Files:**
- Create: `artmind/insights/profile.py`
- Create: `test/_insights_helpers.py` (the `FakeProjector` is needed here; the rest is used from Task 7)
- Test: `test/test_insights_profile.py`

- [ ] **Step 1: Write the helper and the failing test**

`test/_insights_helpers.py` (complete; Task 7 adds nothing to it):

```python
"""Synthetic fixtures for insights tests. Synthetic data only -- never real vault content."""

from datetime import date, timedelta

from artmind.insights.config import parse_config
from artmind.insights.profile import EntityRow, Profile, summarise_class


class FakeProjector:
    def __init__(self, tables: dict):
        self.tables = tables
        self.calls: list[tuple[str, dict]] = []

    def rows(self, name, **params):
        self.calls.append((name, params))
        return list(self.tables.get(name, []))


def year_days(year: int, n: int) -> list[str]:
    """n distinct ISO days spread across `year` (n <= 365)."""
    step = max(1, 365 // max(n, 1))
    return [(date(year, 1, 1) + timedelta(days=i * step)).isoformat() for i in range(n)]


def ent(_id, cls, days, name=None, type=None) -> EntityRow:
    days = tuple(sorted(set(days)))
    return EntityRow(_id=_id, name=name or _id, cls=cls, type=type, days=days, docs=len(days))


def make_ctx(entities, *, schema=None, effective=None, docs_per_period=None, config=None, anchors=(),
             as_of=None, relations=(), open_conflicts=(), properties=None, default_time_share=0.0):
    from artmind.insights.detectors import RunContext

    schema = schema or {}
    decl = schema.get("entity_types") or {}
    classes = {}
    for cls in sorted({e.cls for e in entities} | set(properties or {})):
        rows = [e for e in entities if e.cls == cls]
        cp = summarise_class(cls, (decl.get(cls) or {}).get("kind") or "recurrent", rows, (properties or {}).get(cls))
        if effective and cls in effective:
            cp.effective_kind = effective[cls]
        classes[cls] = cp
    all_days = sorted({d for e in entities for d in e.days})
    profile = Profile(
        domain="demo", classes=classes, relations=list(relations),
        docs_per_period=docs_per_period or {"2022": 100, "2023": 100, "2024": 100},
        default_time_share=default_time_share,
        latest_doc=as_of or (all_days[-1] if all_days else None),
        dated_days=len(all_days), open_conflicts=list(open_conflicts),
    )
    return RunContext(
        domain="demo", schema=schema, config=parse_config(config or {}, "demo"), profile=profile,
        entities=list(entities), anchors=set(anchors), as_of=profile.latest_doc,
    )
```

`test/test_insights_profile.py`:

```python
import pytest

from _insights_helpers import FakeProjector, ent
from artmind.insights.config import parse_config
from artmind.insights.model import InsightsError
from artmind.insights.profile import (
    build_profile, declared_triples, effective_kind, infer_anchors, summarise_class,
)

SCHEMA = {
    "entity_types": {
        "PERSON": {"kind": "recurrent", "relates_to": {
            "LOCATION": ["went_to", "visits"],
            "EVENT": ["participates_in (properties: {role: organiser|participant})"],
        }},
        "EVENT": {"kind": "occurrent"},
    }
}


def _tables():
    return {
        "entity_evidence": [
            {"_id": "p1", "name": "Asha", "cls": "PERSON", "type": "self", "days": ["2023-01-02", "2023-01-01"], "docs": 2},
            {"_id": "e1", "name": "Party", "cls": "EVENT", "type": "Celebration", "days": ["2023-01-02"], "docs": 1},
            {"_id": "x1", "name": "Undated", "cls": "EVENT", "type": None, "days": [None], "docs": 1},
        ],
        "documents": [
            {"id": "d1", "valid_from": "2023-01-01", "time_source": "document_label"},
            {"id": "d2", "valid_from": "2023-01-02", "time_source": "document_label"},
            {"id": "d3", "valid_from": "2024-03-01", "time_source": "default_ingestion"},
        ],
        "property_stats": [
            {"cls": "PERSON", "property": "relationship_to_diarist", "filled": 1, "distinct_values": 1, "sample": ["self"]},
        ],
        "relation_triples": [
            {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 4, "targets": ["l1"]},
            {"rel": "OWNS", "src": "PERSON", "dst": "OBJECT", "n": 2, "targets": ["o1"]},
        ],
        "open_conflicts": [{"cls": "PERSON", "property": "occupation", "n": 3}],
    }


def test_build_profile_measures_classes_documents_and_relations():
    proj = FakeProjector(_tables())
    profile, entities = build_profile(proj, SCHEMA, parse_config({}, "demo"))

    assert [e._id for e in entities] == ["e1", "p1", "x1"]
    assert entities[1].days == ("2023-01-01", "2023-01-02")
    assert entities[2].days == ()
    assert profile.classes["PERSON"].declared_kind == "recurrent"
    assert profile.classes["EVENT"].type_counts == {"celebration": 1}
    assert profile.classes["PERSON"].properties["relationship_to_diarist"]["filled_rate"] == 1.0
    assert profile.docs_per_period == {"2023": 2, "2024": 1}
    assert profile.default_time_share == pytest.approx(1 / 3, abs=1e-4)
    assert profile.latest_doc == "2024-03-01" and profile.dated_days == 3
    assert [(r["rel"], r["declared"]) for r in profile.relations] == [("WENT_TO", True), ("OWNS", False)]
    assert profile.open_conflicts == [{"cls": "PERSON", "property": "occupation", "n": 3}]
    assert ("relation_triples", {"max_targets": 2000}) in proj.calls
    d = profile.to_dict()
    assert "targets" not in d["relations"][0]


def test_declared_triples_parse_verbs_with_annotations():
    assert declared_triples(SCHEMA) == {
        ("WENT_TO", "PERSON", "LOCATION"), ("VISITS", "PERSON", "LOCATION"), ("PARTICIPATES_IN", "PERSON", "EVENT"),
    }


@pytest.mark.parametrize("declared, median, single, n, expected", [
    ("recurrent", 1, 0.9, 50, "occurrent"),
    ("recurrent", 1, 0.9, 5, "recurrent"),
    ("recurrent", 2, 0.5, 50, "recurrent"),
    ("occurrent", 4, 0.1, 50, "recurrent"),
    ("occurrent", 1, 0.95, 50, "occurrent"),
])
def test_effective_kind(declared, median, single, n, expected):
    assert effective_kind(declared, median, single, n) == expected


def test_summarise_class_falls_back_to_documents_when_undated():
    cp = summarise_class("NOTE", "recurrent", [ent("n1", "NOTE", [])], None)
    assert cp.entities == 1 and cp.median_days == 0.0


def test_anchor_inference_rules():
    asha = ent("p1", "PERSON", [f"2024-01-{d:02d}" for d in range(1, 11)], name="Asha", type="self")
    ben = ent("p2", "PERSON", ["2024-01-01"], name="Ben")
    cfg = parse_config({}, "demo")
    assert infer_anchors([asha, ben], cfg, 10) == [{"_id": "p1", "name": "Asha", "rule": "type=self"}]

    asha_untyped = ent("p1", "PERSON", asha.days, name="Asha")
    assert infer_anchors([asha_untyped, ben], cfg, 10) == [{"_id": "p1", "name": "Asha", "rule": "coverage"}]
    assert infer_anchors([ben], cfg, 0) == []

    assert infer_anchors([asha, ben], parse_config({"anchors": ["ben"]}, "demo"), 10) == [
        {"_id": "p2", "name": "Ben", "rule": "config"}]
    assert infer_anchors([asha, ben], parse_config({"anchors": ["@p1"]}, "demo"), 10)[0]["_id"] == "p1"
    with pytest.raises(InsightsError, match="matched 0"):
        infer_anchors([asha, ben], parse_config({"anchors": ["Cleo"]}, "demo"), 10)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_profile.py -q`. Expected: FAIL, `ImportError` from `artmind.insights.profile`.

- [ ] **Step 3: Implement** `artmind/insights/profile.py`

```python
"""Shape profile, effective kind and anchor inference (spec 2026-10-17 §4.2, §4.4)."""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from artmind.insights.config import InsightsConfig
from artmind.insights.model import InsightsError
from artmind.insights.projections import SKIP_PROPERTY_KEYS
from artmind.insights.stats import period_of

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EFFECTIVE_KIND_MIN_ENTITIES = 20
ANCHOR_COVERAGE = 0.8
MAX_RELATION_TARGETS = 2000


@dataclass(frozen=True)
class EntityRow:
    _id: str
    name: str
    cls: str
    type: str | None
    days: tuple[str, ...]  # sorted distinct ISO dates
    docs: int


@dataclass
class ClassProfile:
    name: str
    declared_kind: str | None
    effective_kind: str
    entities: int
    median_days: float
    single_day_share: float
    type_counts: dict[str, int]
    properties: dict[str, dict] = field(default_factory=dict)


@dataclass
class Profile:
    domain: str
    classes: dict[str, ClassProfile]
    relations: list[dict]
    docs_per_period: dict[str, int]
    default_time_share: float
    latest_doc: str | None
    dated_days: int
    open_conflicts: list[dict]

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "classes": {name: asdict(cp) for name, cp in sorted(self.classes.items())},
            "relations": [{k: v for k, v in r.items() if k != "targets"} for r in self.relations],
            "docs_per_period": self.docs_per_period,
            "default_time_share": self.default_time_share,
            "latest_doc": self.latest_doc,
            "dated_days": self.dated_days,
            "open_conflicts": self.open_conflicts,
        }


def _day(value) -> str | None:
    text = str(value)[:10] if value is not None else ""
    return text if _ISO_DAY.match(text) else None


def _iso_days(values) -> tuple[str, ...]:
    return tuple(sorted({d for d in (_day(v) for v in values or []) if d}))


def effective_kind(declared: str | None, median_days: float, single_share: float, entities: int) -> str:
    """The declared kind unless the graph contradicts it (spec §4.2)."""
    declared = declared or "recurrent"
    if entities < EFFECTIVE_KIND_MIN_ENTITIES:
        return declared
    if declared == "recurrent" and median_days <= 1 and single_share >= 0.8:
        return "occurrent"
    if declared == "occurrent" and median_days >= 3:
        return "recurrent"
    return declared


def summarise_class(name: str, declared: str | None, rows: list[EntityRow], properties: dict | None) -> ClassProfile:
    counts = [len(r.days) for r in rows]
    median = float(statistics.median(counts)) if counts else 0.0
    single = sum(1 for c in counts if c <= 1) / len(counts) if counts else 0.0
    types = Counter(r.type.strip().lower() for r in rows if r.type and r.type.strip())
    props = {k: dict(v) for k, v in (properties or {}).items()}
    for stats in props.values():
        stats["filled_rate"] = round(stats.get("filled", 0) / len(rows), 4) if rows else 0.0
    return ClassProfile(
        name=name, declared_kind=declared, effective_kind=effective_kind(declared, median, single, len(rows)),
        entities=len(rows), median_days=median, single_day_share=round(single, 4),
        type_counts=dict(types.most_common(15)), properties=props,
    )


def declared_triples(schema: dict) -> set[tuple[str, str, str]]:
    """{(REL_TYPE, SRC, DST)} from every class's `relates_to`; verbs may carry '(properties: ...)'."""
    out: set[tuple[str, str, str]] = set()
    for src, decl in (schema.get("entity_types") or {}).items():
        for dst, verbs in ((decl or {}).get("relates_to") or {}).items():
            for verb in verbs or []:
                token = re.split(r"[\s(]", str(verb).strip(), maxsplit=1)[0]
                if token:
                    out.add((token.upper(), src, dst))
    return out


def build_profile(projector, schema: dict, config: InsightsConfig) -> tuple[Profile, list[EntityRow]]:
    entities = sorted(
        (
            EntityRow(
                _id=r["_id"], name=r.get("name") or r["_id"], cls=r.get("cls") or "UNKNOWN",
                type=r.get("type") if isinstance(r.get("type"), str) else None,
                days=_iso_days(r.get("days")), docs=int(r.get("docs") or 0),
            )
            for r in projector.rows("entity_evidence")
        ),
        key=lambda e: e._id,
    )
    props: dict[str, dict] = defaultdict(dict)
    for r in projector.rows("property_stats", skip_keys=list(SKIP_PROPERTY_KEYS)):
        props[r.get("cls") or "UNKNOWN"][r["property"]] = {
            "filled": int(r.get("filled") or 0),
            "distinct": int(r.get("distinct_values") or 0),
            "sample": sorted(str(s) for s in r.get("sample") or [] if s is not None),
        }
    by_cls: dict[str, list[EntityRow]] = defaultdict(list)
    for e in entities:
        by_cls[e.cls].append(e)
    decl = schema.get("entity_types") or {}
    classes = {
        cls: summarise_class(cls, (decl.get(cls) or {}).get("kind"), rows, props.get(cls))
        for cls, rows in sorted(by_cls.items())
    }

    docs = projector.rows("documents")
    dated = [d for d in (_day(row.get("valid_from")) for row in docs) if d]
    defaulted = sum(1 for row in docs if not _day(row.get("valid_from")) or row.get("time_source") == "default_ingestion")
    per_period = Counter(period_of(d, config.period) for d in dated)

    declared = declared_triples(schema)
    relations = sorted(
        (
            {"rel": r["rel"], "src": r["src"], "dst": r["dst"], "n": int(r["n"]),
             "declared": (r["rel"], r["src"], r["dst"]) in declared, "targets": list(r.get("targets") or [])}
            for r in projector.rows("relation_triples", max_targets=MAX_RELATION_TARGETS)
        ),
        key=lambda r: (-r["n"], str(r["rel"]), str(r["src"]), str(r["dst"])),
    )
    conflicts = sorted(
        ({"cls": r.get("cls"), "property": r.get("property"), "n": int(r["n"])} for r in projector.rows("open_conflicts")),
        key=lambda r: (-r["n"], str(r["cls"]), str(r["property"])),
    )
    profile = Profile(
        domain=config.domain, classes=classes, relations=relations,
        docs_per_period=dict(sorted(per_period.items())),
        default_time_share=round(defaulted / len(docs), 4) if docs else 1.0,
        latest_doc=max(dated) if dated else None, dated_days=len(set(dated)), open_conflicts=conflicts,
    )
    return profile, entities


def infer_anchors(entities: list[EntityRow], config: InsightsConfig, dated_days: int) -> list[dict]:
    """Config first, then a single PERSON of type `self`, then >= 80% day coverage (spec §4.4)."""
    if config.anchors:
        out = []
        for ref in config.anchors:
            if ref.startswith("@"):
                match = [e for e in entities if e._id == ref[1:]]
            else:
                match = [e for e in entities if e.name.casefold() == ref.casefold()]
            if len(match) != 1:
                raise InsightsError(f"anchor {ref!r} matched {len(match)} entities in {config.domain}; use @<_id>")
            out.append({"_id": match[0]._id, "name": match[0].name, "rule": "config"})
        return out
    selves = [e for e in entities if e.cls == "PERSON" and (e.type or "").strip().lower() == "self"]
    if len(selves) == 1:
        return [{"_id": selves[0]._id, "name": selves[0].name, "rule": "type=self"}]
    if not dated_days:
        return []
    return [
        {"_id": e._id, "name": e.name, "rule": "coverage"}
        for e in entities if len(e.days) / dated_days >= ANCHOR_COVERAGE
    ]
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_profile.py -q`.

Expected: all PASS. `make_ctx` imports `RunContext` lazily and is first used in Task 7.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/profile.py test/_insights_helpers.py test/test_insights_profile.py
git commit -m "feat(insights): shape profile, effective kind, declared triples and anchor inference"
```

---

### Task 7: Detector framework and registry

**Files:**
- Create: `artmind/insights/detectors/__init__.py`
- Create (stubs filled by Tasks 8–13): `salience.py`, `dormancy.py`, `communities.py`, `brokers.py`, `health.py` in `artmind/insights/detectors/`
- Test: `test/test_insights_detectors_base.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights.config import KNOWN_DETECTORS
from artmind.insights.detectors import Skip, registry, subject


def test_registry_order_and_names():
    names = [d.name for d in registry()]
    assert names == [
        "salience_trend", "type_trend", "dormancy", "communities", "brokers",
        "kind_mismatch", "vocabulary_sprawl", "relationship_usage", "open_conflicts",
    ]
    assert set(names) <= KNOWN_DETECTORS
    assert {d.kind for d in registry()} == {"corpus", "health"}


def test_context_helpers():
    a = ent("a", "PERSON", ["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01"])
    b = ent("b", "PERSON", ["2022-01-01"])
    me = ent("me", "PERSON", a.days)
    ctx = make_ctx([a, b, me], anchors={"me"}, docs_per_period={"2021": 5, "2022": 100, "2023": 30})
    assert [e._id for e in ctx.members("PERSON")] == ["a"]
    assert [e._id for e in ctx.members("PERSON", floor=1)] == ["a", "b"]
    assert ctx.scored_periods() == ["2022", "2023"]
    assert ctx.temporal()
    assert ctx.classes_of_kind("recurrent") == ["PERSON"]
    assert subject(a) == {"_id": "a", "name": "a", "class": "PERSON"}


def test_context_not_temporal_when_dates_defaulted():
    ctx = make_ctx([ent("a", "PERSON", ["2022-01-01"])], default_time_share=0.9)
    assert not ctx.temporal()
    assert Skip("x").reason == "x"
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_detectors_base.py -q`. Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`artmind/insights/detectors/__init__.py`:

```python
"""Detector framework (spec 2026-10-17 §5 steps 2-4).

A detector is a name, a kind (corpus | health), a `plan(ctx)` that returns
targets or a Skip with the reason, and a `run(ctx, target)` that returns
findings. Detectors read the RunContext only and never touch the driver.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from artmind.insights.config import InsightsConfig
from artmind.insights.model import Finding
from artmind.insights.profile import EntityRow, Profile


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass
class RunContext:
    domain: str
    schema: dict
    config: InsightsConfig
    profile: Profile
    entities: list[EntityRow]
    anchors: set[str]
    as_of: str | None
    memo: dict = field(default_factory=dict)

    def scored_periods(self) -> list[str]:
        return [p for p, n in sorted(self.profile.docs_per_period.items()) if n >= self.config.min_period_docs]

    def temporal(self) -> bool:
        return self.profile.latest_doc is not None and self.profile.default_time_share < 0.5

    def classes_of_kind(self, kind: str) -> list[str]:
        return [name for name, cp in sorted(self.profile.classes.items()) if cp.effective_kind == kind]

    def members(self, cls: str, floor: int | None = None) -> list[EntityRow]:
        floor = self.config.min_evidence_days if floor is None else floor
        return [e for e in self.entities if e.cls == cls and e._id not in self.anchors and len(e.days) >= floor]


@dataclass(frozen=True)
class Detector:
    name: str
    kind: str
    plan: Callable[[RunContext], "list[str] | Skip"]
    run: Callable[[RunContext, str], list[Finding]]


def subject(e: EntityRow) -> dict:
    return {"_id": e._id, "name": e.name, "class": e.cls}


def registry() -> list[Detector]:
    """Every detector in run order. `brokers` must follow `communities` (it reads its circles)."""
    from artmind.insights.detectors import brokers, communities, dormancy, health, salience

    return [
        salience.SALIENCE_TREND, salience.TYPE_TREND, dormancy.DORMANCY,
        communities.COMMUNITIES, brokers.BROKERS,
        health.KIND_MISMATCH, health.VOCABULARY_SPRAWL, health.RELATIONSHIP_USAGE, health.OPEN_CONFLICTS,
    ]
```

The five detector modules start as stubs, which Tasks 8–13 replace wholesale. Write each stub with this shape: imports, a `plan` returning `Skip("not implemented")`, a `run` returning `[]`, and the `Detector` constants.

`artmind/insights/detectors/salience.py`:

```python
from artmind.insights.detectors import Detector, Skip

SALIENCE_TREND = Detector("salience_trend", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
TYPE_TREND = Detector("type_trend", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

`artmind/insights/detectors/dormancy.py`:

```python
from artmind.insights.detectors import Detector, Skip

DORMANCY = Detector("dormancy", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

`artmind/insights/detectors/communities.py`:

```python
from artmind.insights.detectors import Detector, Skip

COMMUNITIES = Detector("communities", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

`artmind/insights/detectors/brokers.py`:

```python
from artmind.insights.detectors import Detector, Skip

BROKERS = Detector("brokers", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

`artmind/insights/detectors/health.py`:

```python
from artmind.insights.detectors import Detector, Skip

KIND_MISMATCH = Detector("kind_mismatch", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
VOCABULARY_SPRAWL = Detector("vocabulary_sprawl", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
RELATIONSHIP_USAGE = Detector("relationship_usage", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
OPEN_CONFLICTS = Detector("open_conflicts", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --group dev pytest test/test_insights_detectors_base.py test/test_insights_profile.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors test/test_insights_detectors_base.py
git commit -m "feat(insights): detector framework, run context and registry"
```

---

### Task 8: `salience_trend` and `type_trend`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/salience.py`
- Test: `test/test_insights_salience.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx, year_days
from artmind.insights.detectors import Skip
from artmind.insights.detectors.salience import SALIENCE_TREND, TYPE_TREND

OCCURRENT = {"entity_types": {"EVENT": {"kind": "occurrent"}, "PERSON": {"kind": "recurrent"}}}


def test_rising_and_fading_entities_are_found_flat_is_not():
    rising = ent("p1", "PERSON", year_days(2022, 2) + year_days(2023, 6) + year_days(2024, 20), name="Asha")
    fading = ent("p2", "PERSON", year_days(2022, 20) + year_days(2023, 6) + year_days(2024, 2), name="Ben")
    flat = ent("p3", "PERSON", year_days(2022, 8) + year_days(2023, 8) + year_days(2024, 8), name="Cleo")
    ctx = make_ctx([rising, fading, flat])

    found = {f.key: f for f in SALIENCE_TREND.run(ctx, "PERSON")}

    assert set(found) == {"salience_trend:PERSON:p1", "salience_trend:PERSON:p2"}
    up = found["salience_trend:PERSON:p1"]
    assert up.stats["direction"] == "rising" and up.title == "Rising: Asha"
    assert up.stats["rates"] == {"2022": 2.0, "2023": 6.0, "2024": 20.0}
    assert up.card == {"type": "series", "periods": ["2022", "2023", "2024"], "values": [2.0, 6.0, 20.0], "unit": "per 100 documents"}
    assert up.subjects == [{"_id": "p1", "name": "Asha", "class": "PERSON"}]
    assert 0 < up.score <= 1
    assert found["salience_trend:PERSON:p2"].stats["direction"] == "fading"


def test_rates_are_normalised_by_documents_per_period():
    e = ent("p1", "PERSON", year_days(2022, 10) + year_days(2023, 10))
    ctx = make_ctx([e], docs_per_period={"2022": 200, "2023": 50})
    (f,) = SALIENCE_TREND.run(ctx, "PERSON")
    assert f.stats["rates"] == {"2022": 5.0, "2023": 20.0}


def test_low_support_and_anchors_are_ignored():
    thin = ent("p1", "PERSON", year_days(2022, 1) + year_days(2023, 1) + year_days(2024, 5))
    me = ent("me", "PERSON", year_days(2022, 2) + year_days(2023, 6) + year_days(2024, 20))
    ctx = make_ctx([thin, me], anchors={"me"})
    assert SALIENCE_TREND.run(ctx, "PERSON") == []


def test_plan_skips_without_dates_or_periods():
    e = ent("p1", "PERSON", year_days(2022, 5))
    assert isinstance(SALIENCE_TREND.plan(make_ctx([e], default_time_share=0.9)), Skip)
    assert isinstance(SALIENCE_TREND.plan(make_ctx([e], docs_per_period={"2022": 100})), Skip)
    assert SALIENCE_TREND.plan(make_ctx([e])) == ["PERSON"]


def test_type_trend_aggregates_occurrent_entities_by_type():
    events = [ent(f"w{i}", "EVENT", [d], type="Work") for i, d in enumerate(year_days(2022, 2) + year_days(2023, 5) + year_days(2024, 15))]
    events += [ent(f"s{i}", "EVENT", [d], type="sport") for i, d in enumerate(year_days(2022, 6) + year_days(2023, 6) + year_days(2024, 6))]
    ctx = make_ctx(events, schema=OCCURRENT)

    assert TYPE_TREND.plan(ctx) == ["EVENT"]
    (f,) = TYPE_TREND.run(ctx, "EVENT")
    assert f.key == "type_trend:EVENT:work" and f.stats["type"] == "work" and f.stats["direction"] == "rising"
    assert f.subjects == []
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_salience.py -q`. Expected: FAIL, because the stub returns `[]` and `Skip`.

- [ ] **Step 3: Implement** (replace the whole file `artmind/insights/detectors/salience.py`)

```python
"""salience_trend / type_trend: rising and fading entities and occurrent types (spec §6.1)."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import period_of, rate_per_100, support_weight, theil_sen_slope

SMOOTH = 0.5  # added to both sides of the ratio so a zero period cannot divide by zero


def _series(days, periods: list[str], period: str, docs: dict[str, int]) -> tuple[list[float], int]:
    counts = Counter(period_of(d, period) for d in days)
    rates = [round(rate_per_100(counts.get(p, 0), docs[p]), 3) for p in periods]
    return rates, sum(counts.get(p, 0) for p in periods)


def _trend(rates: list[float], support: int, cfg: dict) -> dict | None:
    if support < cfg.get("min_support", 8) or len(rates) < 2:
        return None
    ratio = (rates[-1] + SMOOTH) / (statistics.mean(rates[:-1]) + SMOOTH)
    log_ratio = math.log2(ratio)
    if abs(log_ratio) < cfg.get("min_log_ratio", 0.58):
        return None
    slope = theil_sen_slope(rates[-cfg.get("window", 3):])
    if slope == 0 or (slope > 0) != (log_ratio > 0):
        return None
    return {
        "direction": "rising" if log_ratio > 0 else "fading",
        "ratio": round(ratio, 3), "slope": round(slope, 3), "support": support, "headline": round(ratio, 3),
        "score": round(min(1.0, abs(log_ratio) / 3 * support_weight(support)), 4),
    }


def _temporal_skip(ctx: RunContext) -> Skip | None:
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    if len(ctx.scored_periods()) < 2:
        return Skip(f"fewer than 2 periods with >= {ctx.config.min_period_docs} documents")
    return None


def _card(periods, rates) -> dict:
    return {"type": "series", "periods": periods, "values": rates, "unit": "per 100 documents"}


def _plan_salience(ctx: RunContext):
    skip = _temporal_skip(ctx)
    if skip:
        return skip
    return ctx.classes_of_kind("recurrent") or Skip("no recurrent classes")


def _run_salience(ctx: RunContext, cls: str) -> list[Finding]:
    cfg, periods = ctx.config.detector("salience_trend"), ctx.scored_periods()
    out = []
    for e in ctx.members(cls):
        rates, support = _series(e.days, periods, ctx.config.period, ctx.profile.docs_per_period)
        st = _trend(rates, support, cfg)
        if st is None:
            continue
        score = st.pop("score")
        out.append(Finding(
            key=f"salience_trend:{cls}:{e._id}", detector="salience_trend", kind="corpus", score=score,
            title=f"{st['direction'].capitalize()}: {e.name}", subjects=[subject(e)],
            stats={**st, "rates": dict(zip(periods, rates))}, card=_card(periods, rates),
        ))
    return out


def _plan_types(ctx: RunContext):
    skip = _temporal_skip(ctx)
    if skip:
        return skip
    classes = [c for c in ctx.classes_of_kind("occurrent") if ctx.profile.classes[c].type_counts]
    return classes or Skip("no occurrent classes with a type")


def _run_types(ctx: RunContext, cls: str) -> list[Finding]:
    cfg, periods = ctx.config.detector("type_trend"), ctx.scored_periods()
    by_type: dict[str, set[str]] = defaultdict(set)
    for e in ctx.entities:
        if e.cls == cls and e.type and e.type.strip() and e._id not in ctx.anchors:
            by_type[e.type.strip().lower()].update(e.days)
    out = []
    for t in sorted(by_type):
        rates, support = _series(by_type[t], periods, ctx.config.period, ctx.profile.docs_per_period)
        st = _trend(rates, support, cfg)
        if st is None:
            continue
        score = st.pop("score")
        out.append(Finding(
            key=f"type_trend:{cls}:{t}", detector="type_trend", kind="corpus", score=score,
            title=f"{st['direction'].capitalize()}: {t} ({cls})", subjects=[],
            stats={**st, "type": t, "class": cls, "rates": dict(zip(periods, rates))}, card=_card(periods, rates),
        ))
    return out


SALIENCE_TREND = Detector("salience_trend", "corpus", _plan_salience, _run_salience)
TYPE_TREND = Detector("type_trend", "corpus", _plan_types, _run_types)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_salience.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/salience.py test/test_insights_salience.py
git commit -m "feat(insights): salience_trend and type_trend detectors"
```

---

### Task 9: `dormancy`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/dormancy.py`
- Test: `test/test_insights_dormancy.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, timedelta

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.dormancy import DORMANCY


def every(start: str, step: int, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i * step)).isoformat() for i in range(n)]


def test_quiet_relative_to_own_cadence_fires():
    quiet = ent("p1", "PERSON", every("2023-01-01", 10, 20), name="Asha")   # last 2023-07-10
    steady = ent("p2", "PERSON", every("2023-01-05", 30, 24), name="Ben")   # last 2024-11-25
    ctx = make_ctx([quiet, steady], as_of="2024-12-31")

    (f,) = DORMANCY.run(ctx, "PERSON")

    assert f.key == "dormancy:PERSON:p1" and f.title == "Quiet: Asha"
    assert f.stats == {"last_seen": "2023-07-10", "cadence_days": 10.0, "silence_days": 540, "support": 20, "headline": 540}
    assert 0 < f.score <= 1


def test_thresholds_come_from_config():
    quiet = ent("p1", "PERSON", every("2024-01-01", 10, 20))  # last 2024-07-09
    ctx = make_ctx([quiet], as_of="2024-09-30", config={"detectors": {"dormancy": {"min_silence_days": 120}}})
    assert DORMANCY.run(ctx, "PERSON") == []


def test_same_day_entities_and_anchors_are_skipped():
    burst = ent("p1", "PERSON", ["2023-01-01"] * 5)
    me = ent("me", "PERSON", every("2023-01-01", 10, 20))
    ctx = make_ctx([burst, me], anchors={"me"}, as_of="2024-12-31")
    assert DORMANCY.run(ctx, "PERSON") == []


def test_plan_needs_dates():
    e = ent("p1", "PERSON", every("2023-01-01", 10, 20))
    assert isinstance(DORMANCY.plan(make_ctx([e], default_time_share=0.9)), Skip)
    assert DORMANCY.plan(make_ctx([e])) == ["PERSON"]
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_dormancy.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/dormancy.py`)

```python
"""dormancy: entities gone quiet relative to their own rhythm (spec §6.2).

`as_of` is the latest document date in the domain, not today, so a vault
that has not been ingested lately does not make everyone look dormant.
"""

from __future__ import annotations

import math

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, median_gap_days, support_weight


def _plan(ctx: RunContext):
    if not ctx.temporal() or ctx.as_of is None:
        return Skip("no document dates (valid time mostly defaulted)")
    return ctx.classes_of_kind("recurrent") or Skip("no recurrent classes")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("dormancy")
    multiple, min_silence = cfg.get("silence_multiple", 3), cfg.get("min_silence_days", 60)
    out = []
    for e in ctx.members(cls):
        cadence = median_gap_days(e.days)
        if cadence <= 0:
            continue
        silence = days_between(e.days[-1], ctx.as_of)
        if silence <= multiple * cadence or silence <= min_silence:
            continue
        score = min(1.0, math.log(silence / cadence) / math.log(30)) * support_weight(len(e.days))
        out.append(Finding(
            key=f"dormancy:{cls}:{e._id}", detector="dormancy", kind="corpus", score=round(score, 4),
            title=f"Quiet: {e.name}", subjects=[subject(e)],
            stats={"last_seen": e.days[-1], "cadence_days": cadence, "silence_days": silence,
                   "support": len(e.days), "headline": silence},
            card={"type": "ranking", "columns": ["last_seen", "cadence_days", "silence_days"]},
        ))
    return out


DORMANCY = Detector("dormancy", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_dormancy.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/dormancy.py test/test_insights_dormancy.py
git commit -m "feat(insights): dormancy detector"
```

---

### Task 10: Graph helpers

**Files:**
- Create: `artmind/insights/graphs.py`
- Test: `test/test_insights_graphs.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from _insights_helpers import ent
from artmind.insights import graphs


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da) for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db) for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13])
    return a + b + [x]


def test_co_mention_weights_and_floor():
    p = ent("p", "PERSON", ["2024-01-01", "2024-01-02", "2024-01-03"])
    q = ent("q", "PERSON", ["2024-01-01", "2024-01-02"])
    r = ent("r", "PERSON", ["2024-01-03"])
    g = graphs.co_mention_graph([p, q, r], min_weight=2)
    assert sorted(g.edges(data="weight")) == [("p", "q", 2)]
    assert g["p"]["q"]["distance"] == 0.5
    assert g.nodes["p"]["name"] == "p"


def test_louvain_finds_the_planted_groups_deterministically():
    g = graphs.co_mention_graph(two_groups())
    first, second = graphs.louvain(g), graphs.louvain(g)
    assert first == second
    sets = [set(c) for c in first]
    assert len(sets) == 2
    assert any({"a1", "a2", "a3", "a4"} <= s for s in sets)
    assert any({"b1", "b2", "b3", "b4"} <= s for s in sets)
    assert graphs.modularity(g, first) > 0.3


def test_restrict_keeps_one_period():
    rows = graphs.restrict(two_groups(), "2023", "year")
    assert all(d.startswith("2023") for e in rows for d in e.days)


def test_lead_and_weighted_degree():
    g = graphs.co_mention_graph(two_groups())
    wdeg = graphs.weighted_degree(g)
    assert graphs.lead(["a1", "a2"], wdeg) == "a1"


def test_caps(monkeypatch):
    monkeypatch.setattr(graphs, "MAX_NODES", 3)
    with pytest.raises(graphs.GraphTooLarge, match="raise min_evidence_days"):
        graphs.co_mention_graph(two_groups())


def test_empty_graph():
    g = graphs.co_mention_graph([])
    assert graphs.louvain(g) == [] and graphs.modularity(g, []) == 0.0
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_graphs.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/graphs.py`

```python
"""networkx helpers for insights (spec §6.3-§6.4, §8). Pure-Python algorithms only:
no numpy/scipy. Inputs are inserted in sorted order and Louvain is seeded, so
the same rows always give the same communities."""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import replace

import networkx as nx
from networkx.algorithms import community as nx_comm

from artmind.insights.model import InsightsError
from artmind.insights.profile import EntityRow
from artmind.insights.stats import period_of

MAX_NODES = 5000
MAX_EDGES = 200_000
SEED = 7


class GraphTooLarge(InsightsError):
    pass


def co_mention_graph(members: list[EntityRow], min_weight: int = 2) -> nx.Graph:
    """Entities linked by the number of evidence days they share."""
    ordered = sorted(members, key=lambda e: e._id)
    by_day: dict[str, list[str]] = defaultdict(list)
    for e in ordered:
        for d in e.days:
            by_day[d].append(e._id)
    weights: Counter = Counter()
    for d in sorted(by_day):
        weights.update(itertools.combinations(by_day[d], 2))
    kept = sorted((pair, w) for pair, w in weights.items() if w >= min_weight)
    nodes = sorted({n for (a, b), _ in kept for n in (a, b)})
    if len(nodes) > MAX_NODES or len(kept) > MAX_EDGES:
        raise GraphTooLarge(
            f"co-mention graph has {len(nodes)} nodes / {len(kept)} edges "
            f"(caps {MAX_NODES} / {MAX_EDGES}); raise min_evidence_days"
        )
    names = {e._id: e.name for e in ordered}
    g = nx.Graph()
    for n in nodes:
        g.add_node(n, name=names[n])
    for (a, b), w in kept:
        g.add_edge(a, b, weight=w, distance=1.0 / w)
    return g


def louvain(g: nx.Graph, resolution: float = 1.0) -> list[frozenset[str]]:
    if g.number_of_edges() == 0:
        return []
    comms = nx_comm.louvain_communities(g, weight="weight", resolution=resolution, seed=SEED)
    return sorted((frozenset(c) for c in comms), key=lambda c: (-len(c), min(c)))


def modularity(g: nx.Graph, comms: list[frozenset[str]]) -> float:
    if not comms or g.number_of_edges() == 0:
        return 0.0
    return float(nx_comm.modularity(g, comms, weight="weight"))


def weighted_degree(g: nx.Graph) -> dict[str, float]:
    return {n: float(d) for n, d in g.degree(weight="weight")}


def lead(members: Iterable[str], wdeg: dict[str, float]) -> str:
    return sorted(members, key=lambda n: (-wdeg.get(n, 0.0), n))[0]


def jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def restrict(members: list[EntityRow], keep: str, period: str) -> list[EntityRow]:
    out = []
    for e in members:
        days = tuple(d for d in e.days if period_of(d, period) == keep)
        if days:
            out.append(replace(e, days=days, docs=len(days)))
    return out
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_graphs.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/graphs.py test/test_insights_graphs.py
git commit -m "feat(insights): co-mention graph, seeded Louvain and graph helpers"
```

---

### Task 11: `communities`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/communities.py`
- Test: `test/test_insights_communities.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights import graphs
from artmind.insights.detectors import Skip
from artmind.insights.detectors.communities import COMMUNITIES

DOCS = {"2023": 100, "2024": 100}


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da, name=f"A{i}") for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db, name=f"B{i}") for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13], name="X")
    return a + b + [x]


def test_circles_shares_and_cohesion():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS)
    assert COMMUNITIES.plan(ctx) == ["PERSON"]

    found = COMMUNITIES.run(ctx, "PERSON")

    circles = [f for f in found if ":circle:" in f.key]
    assert len(circles) == 2
    member_sets = [set(f.stats["members"]) for f in circles]
    assert any({"a1", "a2", "a3", "a4"} <= s for s in member_sets)
    assert any({"b1", "b2", "b3", "b4"} <= s for s in member_sets)
    net = circles[0].card
    assert net["type"] == "network" and net["nodes"] and net["edges"]
    assert {f.key.split(":")[2] for f in found} == {"circle", "share", "cohesion"}
    share = next(f for f in found if ":share:" in f.key)
    assert set(share.stats["rates"]) == {"2023", "2024"}
    _, memo_circles = ctx.memo[("circles", "PERSON")]
    assert {c["id"] for c in memo_circles} == {f"PERSON:{f.key.rsplit(':', 1)[1]}" for f in circles}
    assert all(0 <= f.score <= 1 for f in found)


def test_deterministic():
    one = [f.to_dict() for f in COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")]
    two = [f.to_dict() for f in COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")]
    assert one == two


def test_no_dates_means_circles_only():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS, default_time_share=0.9)
    assert {f.key.split(":")[2] for f in COMMUNITIES.run(ctx, "PERSON")} == {"circle"}


def test_too_large_becomes_a_health_finding(monkeypatch):
    monkeypatch.setattr(graphs, "MAX_NODES", 3)
    (f,) = COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")
    assert f.key == "communities:PERSON:too_large" and f.kind == "health" and f.action == "config"


def test_plan_prefers_configured_classes_and_needs_members():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS, config={"detectors": {"communities": {"classes": ["LOCATION"]}}})
    assert isinstance(COMMUNITIES.plan(ctx), Skip)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_communities.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/communities.py`)

```python
"""communities: circles in the co-mention graph, their share over time, and cohesion (spec §6.3).

Leaves `ctx.memo[("circles", cls)] = (graph, circles)` for `brokers`. A circle's
key is built from its lead member's _id, so it is stable while the lead holds.
diff.py re-keys circles whose lead changed by member Jaccard.
"""

from __future__ import annotations

from collections import Counter

from artmind.insights import graphs
from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.stats import period_of, rate_per_100, theil_sen_slope

NETWORK_CARD_NODES = 60


def _classes(ctx: RunContext) -> list[str]:
    wanted = ctx.config.detector("communities").get("classes")
    if wanted:
        return [c for c in wanted if c in ctx.profile.classes]
    recurrent = ctx.classes_of_kind("recurrent")
    if "PERSON" in recurrent:
        return ["PERSON"]
    return sorted(recurrent, key=lambda c: (-len(ctx.members(c)), c))[:1]


def _plan(ctx: RunContext):
    classes = [c for c in _classes(ctx) if len(ctx.members(c)) >= 3]
    return classes or Skip("no class with >= 3 entities above the evidence floor")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("communities")
    min_weight, resolution = cfg.get("min_weight", 2), cfg.get("resolution", 1.0)
    members = ctx.members(cls)
    try:
        g = graphs.co_mention_graph(members, min_weight)
    except graphs.GraphTooLarge as exc:
        return [Finding(
            key=f"communities:{cls}:too_large", detector="communities", kind="health", score=1.0,
            title=f"{cls} co-mention graph is too large to analyse", stats={"reason": str(exc), "headline": 0},
            card={"type": "stat"}, action="config",
        )]
    comms = [c for c in graphs.louvain(g, resolution) if len(c) >= cfg.get("min_circle_size", 3)]
    if not comms:
        return []
    wdeg = graphs.weighted_degree(g)
    total = sum(wdeg.values()) or 1.0
    circles, out = [], []
    for c in comms:
        ld = graphs.lead(c, wdeg)
        ordered = sorted(c, key=lambda n: (-wdeg[n], n))
        name = f"Circle around {g.nodes[ld]['name']}"
        circles.append({"id": f"{cls}:{ld}", "class": cls, "lead": ld, "name": name,
                        "name_source": "template", "members": ordered})
        top = ordered[:NETWORK_CARD_NODES]
        sub = g.subgraph(top)
        out.append(Finding(
            key=f"communities:{cls}:circle:{ld}", detector="communities", kind="corpus",
            score=round(min(1.0, sum(wdeg[n] for n in c) / total), 4),
            title=f"{name} ({len(c)})",
            subjects=[{"_id": n, "name": g.nodes[n]["name"], "class": cls} for n in ordered[:10]],
            stats={"members": ordered, "size": len(c), "headline": len(c)},
            card={"type": "network",
                  "nodes": [{"_id": n, "name": g.nodes[n]["name"], "weight": wdeg[n]} for n in top],
                  "edges": [{"source": a, "target": b, "weight": d["weight"]} for a, b, d in sorted(sub.edges(data=True))]},
        ))
    ctx.memo[("circles", cls)] = (g, circles)
    if ctx.temporal() and len(ctx.scored_periods()) >= 2:
        out += _shares(ctx, cls, members, circles)
        out += _cohesion(ctx, cls, members, min_weight, resolution)
    return out


def _shares(ctx: RunContext, cls: str, members, circles) -> list[Finding]:
    periods, docs = ctx.scored_periods(), ctx.profile.docs_per_period
    by_id = {e._id: e for e in members}
    out = []
    for c in circles:
        counts = Counter(period_of(d, ctx.config.period) for n in c["members"] for d in by_id[n].days)
        rates = [round(rate_per_100(counts.get(p, 0), docs[p]), 3) for p in periods]
        shift = max(abs(b - a) for a, b in zip(rates, rates[1:]))
        first, last = rates[0], rates[-1]
        out.append(Finding(
            key=f"communities:{cls}:share:{c['lead']}", detector="communities", kind="corpus",
            score=round(min(1.0, abs(last - first) / max(first, last, 1.0)), 4),
            title=f"{c['name']}: share of mentions",
            subjects=[{"_id": c["lead"], "name": by_id[c["lead"]].name, "class": cls}],
            stats={"members": c["members"], "rates": dict(zip(periods, rates)), "max_shift": round(shift, 3), "headline": last},
            card={"type": "series", "periods": periods, "values": rates, "unit": "mention-days per 100 documents"},
        ))
    return out


def _cohesion(ctx: RunContext, cls: str, members, min_weight: int, resolution: float) -> list[Finding]:
    periods, values = ctx.scored_periods(), []
    for p in periods:
        gp = graphs.co_mention_graph(graphs.restrict(members, p, ctx.config.period), min_weight)
        values.append(round(graphs.modularity(gp, graphs.louvain(gp, resolution)), 4))
    slope = theil_sen_slope(values)
    return [Finding(
        key=f"communities:{cls}:cohesion", detector="communities", kind="corpus",
        score=round(min(1.0, abs(slope) * 10), 4),
        title=f"{cls} circles are {'merging' if slope < 0 else 'separating'}",
        stats={"modularity": dict(zip(periods, values)), "slope": round(slope, 4), "headline": values[-1]},
        card={"type": "series", "periods": periods, "values": values, "unit": "modularity"},
    )]


COMMUNITIES = Detector("communities", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_communities.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/communities.py test/test_insights_communities.py
git commit -m "feat(insights): communities detector (circles, shares, cohesion)"
```

---

### Task 12: `brokers`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/brokers.py`
- Test: `test/test_insights_brokers.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.brokers import BROKERS
from artmind.insights.detectors.communities import COMMUNITIES

DOCS = {"2023": 100, "2024": 100}


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da) for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db) for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13], name="X")
    return a + b + [x]


def test_bridge_node_is_the_top_broker():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS)
    COMMUNITIES.run(ctx, "PERSON")
    assert BROKERS.plan(ctx) == ["PERSON"]

    found = BROKERS.run(ctx, "PERSON")

    assert found[0].key == "brokers:PERSON:x" and found[0].score == 1.0
    assert found[0].title == "Connects 2 circles: X"
    assert len(found[0].stats["circles"]) == 2


def test_plan_skips_when_communities_did_not_run():
    assert isinstance(BROKERS.plan(make_ctx(two_groups(), docs_per_period=DOCS)), Skip)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_brokers.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/brokers.py`)

```python
"""brokers: entities whose neighbours span circles, ranked by betweenness (spec §6.4)."""

from __future__ import annotations

import networkx as nx

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding

EXACT_BETWEENNESS_MAX_NODES = 2000  # above this, sample k pivots (seeded)


def _plan(ctx: RunContext):
    classes = sorted(key[1] for key in ctx.memo if isinstance(key, tuple) and key[0] == "circles")
    return classes or Skip("communities did not run")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    g, circles = ctx.memo[("circles", cls)]
    if len(circles) < 2:
        return []
    circle_of = {n: c["id"] for c in circles for n in c["members"]}
    names = {c["id"]: c["name"] for c in circles}
    k = None if g.number_of_nodes() <= EXACT_BETWEENNESS_MAX_NODES else 500
    bt = nx.betweenness_centrality(g, weight="distance", k=k, seed=7)
    candidates = []
    for n in sorted(g):
        spans = {circle_of[m] for m in g[n] if m in circle_of}
        if n in circle_of:
            spans.add(circle_of[n])
        if len(spans) >= 2 and bt[n] > 0:
            candidates.append((n, sorted(spans)))
    top = max((bt[n] for n, _ in candidates), default=0.0)
    out = []
    for n, spans in candidates:
        out.append(Finding(
            key=f"brokers:{cls}:{n}", detector="brokers", kind="corpus", score=round(bt[n] / top, 4),
            title=f"Connects {len(spans)} circles: {g.nodes[n]['name']}",
            subjects=[{"_id": n, "name": g.nodes[n]["name"], "class": cls}],
            stats={"betweenness": round(bt[n], 4), "circles": [names[s] for s in spans],
                   "degree": g.degree(n), "headline": round(bt[n], 4)},
            card={"type": "ranking", "columns": ["betweenness", "circles"]},
        ))
    return sorted(out, key=lambda f: (-f.score, f.key))


BROKERS = Detector("brokers", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_brokers.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/brokers.py test/test_insights_brokers.py
git commit -m "feat(insights): brokers detector"
```

---

### Task 13: Graph-health detectors

**Files:**
- Modify (replace stub): `artmind/insights/detectors/health.py`
- Test: `test/test_insights_health.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights.detectors.health import (
    KIND_MISMATCH, OPEN_CONFLICTS, RELATIONSHIP_USAGE, VOCABULARY_SPRAWL, hint_options,
)


def test_kind_mismatch_flags_a_recurrent_class_seen_once_each():
    acts = [ent(f"a{i}", "ACTIVITY", [f"2024-01-{(i % 28) + 1:02d}"]) for i in range(25)]
    ctx = make_ctx(acts, schema={"entity_types": {"ACTIVITY": {"kind": "recurrent"}}})
    assert KIND_MISMATCH.plan(ctx) == ["all"]
    (f,) = KIND_MISMATCH.run(ctx, "all")
    assert f.key == "kind_mismatch:ACTIVITY" and f.kind == "health" and f.action == "schema"
    assert f.stats["declared"] == "recurrent" and f.stats["effective"] == "occurrent"


def test_hint_options():
    assert hint_options("firm intention, tentative, wishful") == ["firm intention", "tentative", "wishful"]
    assert hint_options("mild, moderate, strong") == ["mild", "moderate", "strong"]
    assert hint_options('e.g., "wife", "son"') == []
    assert hint_options("health, mood, life situation as mentioned") == []
    assert hint_options("why this plan matters") == []
    assert hint_options(None) == []


def test_vocabulary_sprawl_on_hinted_and_type_properties():
    schema = {"entity_types": {"PLAN": {"kind": "recurrent", "type_examples": ["work", "family"], "properties": {
        "status": {"hint": "firm intention, tentative, wishful"},
        "motivation": {"hint": "why this plan matters"},
        "urgency": {},
    }}}}
    props = {"PLAN": {
        "status": {"filled": 100, "distinct": 50, "sample": ["Firm intention", "firm intention", "tentative"]},
        "motivation": {"filled": 100, "distinct": 95, "sample": ["a", "b"]},
        "urgency": {"filled": 10, "distinct": 3, "sample": ["low"]},
        "type": {"filled": 100, "distinct": 8, "sample": ["work", "Work", "family"]},
    }}
    ctx = make_ctx([ent("p1", "PLAN", ["2024-01-01"])], schema=schema, properties=props)

    found = {f.key: f for f in VOCABULARY_SPRAWL.run(ctx, "all")}

    assert set(found) == {"vocabulary_sprawl:PLAN:status", "vocabulary_sprawl:PLAN:type"}
    status = found["vocabulary_sprawl:PLAN:status"]
    assert status.stats["expected_max"] == 15 and status.stats["basis"] == "hint options"
    assert status.stats["near_duplicates"] == [["Firm intention", "firm intention"]]
    assert found["vocabulary_sprawl:PLAN:type"].stats["distinct"] == 8   # within 5x2, flagged for near-duplicates
    assert found["vocabulary_sprawl:PLAN:type"].stats["near_duplicates"] == [["Work", "work"]]


def test_relationship_usage():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "relates_to": {"LOCATION": ["went_to", "visits", "lives_at"]}}}}
    rels = [
        {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 20, "declared": True, "targets": [f"l{i}" for i in range(10)]},
        {"rel": "VISITS", "src": "PERSON", "dst": "LOCATION", "n": 15, "declared": True, "targets": [f"l{i}" for i in range(8)]},
        {"rel": "WORKS_AT", "src": "PERSON", "dst": "PLACE", "n": 6, "declared": False, "targets": ["x"]},
        {"rel": "OWNS", "src": "PERSON", "dst": "OBJECT", "n": 2, "declared": False, "targets": ["o"]},
    ]
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], schema=schema, relations=rels)

    found = {f.key: f for f in RELATIONSHIP_USAGE.run(ctx, "all")}

    assert set(found) == {
        "relationship_usage:undeclared", "relationship_usage:unused",
        "relationship_usage:overlap:PERSON:VISITS:WENT_TO:LOCATION",
    }
    assert found["relationship_usage:undeclared"].stats["triples"] == [{"rel": "WORKS_AT", "src": "PERSON", "dst": "PLACE", "n": 6}]
    assert found["relationship_usage:unused"].stats["triples"] == [{"rel": "LIVES_AT", "src": "PERSON", "dst": "LOCATION"}]
    assert found["relationship_usage:overlap:PERSON:VISITS:WENT_TO:LOCATION"].stats["overlap"] == 1.0


def test_open_conflicts_grouped_by_class():
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], open_conflicts=[
        {"cls": "PERSON", "property": "occupation", "n": 30}, {"cls": "PERSON", "property": "age", "n": 10},
        {"cls": None, "property": "x", "n": 1},
    ])
    found = {f.key: f for f in OPEN_CONFLICTS.run(ctx, "all")}
    assert found["open_conflicts:PERSON"].stats == {"open": 40, "by_property": {"occupation": 30, "age": 10}, "headline": 40}
    assert found["open_conflicts:PERSON"].action == "curate"
    assert "open_conflicts:UNKNOWN" in found
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_health.py -q`. Expected: FAIL, `ImportError: hint_options`.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/health.py`)

```python
"""Graph-health detectors (spec §7): where the graph and its schema disagree.

Each finding carries an `action` (schema | curate) pointing at the fix.
"""

from __future__ import annotations

import re
from collections import defaultdict

from artmind.insights.detectors import Detector, RunContext
from artmind.insights.model import Finding
from artmind.insights.profile import declared_triples


def _all(ctx: RunContext) -> list[str]:
    return ["all"]


# ── kind_mismatch ─────────────────────────────────────────────────────────────

def _run_kind(ctx: RunContext, _target: str) -> list[Finding]:
    out = []
    for cls, cp in sorted(ctx.profile.classes.items()):
        if not cp.declared_kind or cp.effective_kind == cp.declared_kind:
            continue
        score = cp.single_day_share if cp.effective_kind == "occurrent" else min(1.0, cp.median_days / 10)
        out.append(Finding(
            key=f"kind_mismatch:{cls}", detector="kind_mismatch", kind="health", score=round(score, 4),
            title=f"{cls} is declared {cp.declared_kind} but behaves {cp.effective_kind}",
            stats={"declared": cp.declared_kind, "effective": cp.effective_kind, "entities": cp.entities,
                   "median_days": cp.median_days, "single_day_share": cp.single_day_share, "headline": cp.median_days},
            card={"type": "stat"}, action="schema",
        ))
    return out


# ── vocabulary_sprawl ─────────────────────────────────────────────────────────

def hint_options(hint) -> list[str]:
    """A hint written as a closed list ('mild, moderate, strong') -> its options; else []."""
    if not isinstance(hint, str) or "e.g" in hint.lower():
        return []
    parts = [p.strip() for p in re.split(r",|\bor\b", hint) if p.strip()]
    if len(parts) >= 2 and all(len(p.split()) <= 3 for p in parts):
        return parts
    return []


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value.strip().casefold())


def _near_duplicates(sample: list[str]) -> list[list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for v in sample:
        groups[_norm(v)].add(v)
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)[:10]


def _expected(schema: dict, cls: str, prop: str) -> tuple[int, str] | None:
    decl = (schema.get("entity_types") or {}).get(cls) or {}
    if prop == "type":
        examples = decl.get("type_examples") or []
        return (len(examples) * 5, "type_examples") if examples else (40, "no type_examples")
    spec = (decl.get("properties") or {}).get(prop)
    options = hint_options(spec.get("hint") if isinstance(spec, dict) else None)
    return (max(10, len(options) * 5), "hint options") if options else None


def _run_sprawl(ctx: RunContext, _target: str) -> list[Finding]:
    out = []
    for cls, cp in sorted(ctx.profile.classes.items()):
        for prop, st in sorted(cp.properties.items()):
            expected = _expected(ctx.schema, cls, prop)
            if expected is None:
                continue
            limit, basis = expected
            dups = _near_duplicates(st.get("sample") or [])
            distinct = st.get("distinct", 0)
            if distinct <= limit and not dups:
                continue
            score = min(1.0, distinct / (limit * 4)) if distinct > limit else 0.3
            out.append(Finding(
                key=f"vocabulary_sprawl:{cls}:{prop}", detector="vocabulary_sprawl", kind="health",
                score=round(score, 4), title=f"{cls}.{prop} has {distinct} distinct values",
                stats={"distinct": distinct, "expected_max": limit, "basis": basis, "near_duplicates": dups,
                       "sample": (st.get("sample") or [])[:20], "headline": distinct},
                card={"type": "stat"}, action="schema",
            ))
    return out


# ── relationship_usage ────────────────────────────────────────────────────────

def _run_relations(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("relationship_usage")
    min_count, min_overlap_n = cfg.get("min_count", 5), cfg.get("min_overlap_count", 10)
    rels = ctx.profile.relations
    total = sum(r["n"] for r in rels) or 1
    out = []
    undeclared = [r for r in rels if not r["declared"] and r["n"] >= min_count]
    if undeclared:
        out.append(Finding(
            key="relationship_usage:undeclared", detector="relationship_usage", kind="health",
            score=round(min(1.0, 2 * sum(r["n"] for r in undeclared) / total), 4),
            title=f"{len(undeclared)} relationship triples are used but not declared",
            stats={"triples": [{k: r[k] for k in ("rel", "src", "dst", "n")} for r in undeclared[:25]],
                   "count": len(undeclared), "headline": len(undeclared)},
            card={"type": "ranking"}, action="schema",
        ))
    declared = declared_triples(ctx.schema)
    observed = {(r["rel"], r["src"], r["dst"]) for r in rels}
    unused = sorted(declared - observed)
    if unused and rels:
        out.append(Finding(
            key="relationship_usage:unused", detector="relationship_usage", kind="health",
            score=round(min(1.0, len(unused) / len(declared)), 4),
            title=f"{len(unused)} declared relationships were never extracted",
            stats={"triples": [{"rel": r, "src": s, "dst": d} for r, s, d in unused[:50]],
                   "count": len(unused), "headline": len(unused)},
            card={"type": "ranking"}, action="schema",
        ))
    by_ends: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rels:
        if r["n"] >= min_overlap_n and r["targets"]:
            by_ends[(r["src"], r["dst"])].append(r)
    for (src, dst), group in sorted(by_ends.items()):
        group = sorted(group, key=lambda r: r["rel"])
        for i, r1 in enumerate(group):
            for r2 in group[i + 1:]:
                a, b = set(r1["targets"]), set(r2["targets"])
                overlap = len(a & b) / min(len(a), len(b))
                if overlap <= 0.5:
                    continue
                out.append(Finding(
                    key=f"relationship_usage:overlap:{src}:{r1['rel']}:{r2['rel']}:{dst}",
                    detector="relationship_usage", kind="health", score=round(overlap, 4),
                    title=f"{r1['rel']} and {r2['rel']} ({src} -> {dst}) point at the same targets",
                    stats={"overlap": round(overlap, 3), "counts": {r1["rel"]: r1["n"], r2["rel"]: r2["n"]},
                           "headline": round(overlap, 3)},
                    card={"type": "stat"}, action="schema",
                ))
    return out


# ── open_conflicts ────────────────────────────────────────────────────────────

def _run_conflicts(ctx: RunContext, _target: str) -> list[Finding]:
    by_cls: dict[str, dict[str, int]] = defaultdict(dict)
    for r in ctx.profile.open_conflicts:
        by_cls[r["cls"] or "UNKNOWN"][str(r["property"])] = r["n"]
    out = []
    for cls, props in sorted(by_cls.items()):
        n = sum(props.values())
        top = dict(sorted(props.items(), key=lambda kv: (-kv[1], kv[0]))[:10])
        out.append(Finding(
            key=f"open_conflicts:{cls}", detector="open_conflicts", kind="health",
            score=round(min(1.0, n / 100), 4), title=f"{n} open conflicts on {cls}",
            stats={"open": n, "by_property": top, "headline": n}, card={"type": "ranking"}, action="curate",
        ))
    return out


KIND_MISMATCH = Detector("kind_mismatch", "health", _all, _run_kind)
VOCABULARY_SPRAWL = Detector("vocabulary_sprawl", "health", _all, _run_sprawl)
RELATIONSHIP_USAGE = Detector("relationship_usage", "health", _all, _run_relations)
OPEN_CONFLICTS = Detector("open_conflicts", "health", _all, _run_conflicts)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_health.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/health.py test/test_insights_health.py
git commit -m "feat(insights): kind_mismatch, vocabulary_sprawl, relationship_usage, open_conflicts"
```

---

### Task 14: Diff

**Files:**
- Create: `artmind/insights/diff.py`
- Test: `test/test_insights_diff.py`

- [ ] **Step 1: Write the failing test**

```python
from artmind.insights.diff import apply_diff
from artmind.insights.model import Finding


def f(key, score=0.5, headline=10, members=None, detector="dormancy", diff="new"):
    stats = {"headline": headline}
    if members is not None:
        stats["members"] = members
    return Finding(key=key, detector=detector, kind="corpus", score=score, title=key, stats=stats, diff=diff)


def test_first_run_marks_everything_new():
    out, summary = apply_diff([f("a"), f("b")], None, None)
    assert [x.diff for x in out] == ["new", "new"]
    assert summary == {"previous": None, "new": 2, "changed": 0, "same": 0, "gone": 0}


def test_new_changed_same_gone():
    prev = [f("same"), f("score", score=0.2), f("head", headline=10), f("old"), f("was_gone", diff="gone")]
    cur = [f("same"), f("score", score=0.5), f("head", headline=20), f("fresh")]

    out, summary = apply_diff(cur, prev, "r1.json")

    states = {x.key: x.diff for x in out}
    assert states == {"same": "same", "score": "changed", "head": "changed", "fresh": "new", "old": "gone"}
    assert summary == {"previous": "r1.json", "new": 1, "changed": 2, "same": 1, "gone": 1}


def test_circle_keeps_its_key_when_its_lead_changes():
    prev = [f("communities:PERSON:circle:a1", members=["a1", "a2", "a3", "a4"], detector="communities")]
    cur = [f("communities:PERSON:circle:a2", members=["a2", "a1", "a3", "a5"], detector="communities")]

    out, summary = apply_diff(cur, prev, "r1.json")

    assert [x.key for x in out] == ["communities:PERSON:circle:a1"]
    assert summary["gone"] == 0 and summary["new"] == 0


def test_dissimilar_circle_is_new():
    prev = [f("communities:PERSON:circle:a1", members=["a1", "a2", "a3"], detector="communities")]
    cur = [f("communities:PERSON:circle:b1", members=["b1", "b2", "b3"], detector="communities")]
    out, summary = apply_diff(cur, prev, "r1.json")
    assert summary["new"] == 1 and summary["gone"] == 1
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_diff.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/diff.py`

```python
"""Diff a run's findings against the previous snapshot (spec §9.3)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from artmind.insights.graphs import jaccard
from artmind.insights.model import Finding

REKEY_JACCARD = 0.3
SCORE_DELTA = 0.15
HEADLINE_DELTA = 0.25


def _prefix(key: str) -> str:
    return key.rsplit(":", 1)[0]


def _rekey(current: list[Finding], previous: list[Finding]) -> None:
    """A membership finding (circle, share) whose key is new adopts the previous key
    of the most similar membership finding with the same prefix (Jaccard >= 0.3)."""
    prev_keys = {p.key for p in previous}
    by_prefix: dict[str, list[Finding]] = defaultdict(list)
    for p in previous:
        if "members" in p.stats:
            by_prefix[_prefix(p.key)].append(p)
    taken = {c.key for c in current}
    for c in current:
        if "members" not in c.stats or c.key in prev_keys:
            continue
        best = None
        for p in by_prefix.get(_prefix(c.key), []):
            if p.key in taken:
                continue
            score = jaccard(set(c.stats["members"]), set(p.stats["members"]))
            if score >= REKEY_JACCARD and (best is None or score > best[0]):
                best = (score, p.key)
        if best:
            taken.discard(c.key)
            c.key = best[1]
            taken.add(c.key)


def _changed(cur: Finding, prev: Finding) -> bool:
    if abs(cur.score - prev.score) >= SCORE_DELTA:
        return True
    a, b = cur.stats.get("headline"), prev.stats.get("headline")
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    if not numeric:
        return False
    base = max(abs(a), abs(b))
    return base > 0 and abs(a - b) / base >= HEADLINE_DELTA


def apply_diff(current: list[Finding], previous: list[Finding] | None, previous_name: str | None):
    """Mark each current finding new/changed/same and append previous findings now gone.
    Findings already `gone` in the previous run are dropped for good."""
    if previous is None:
        for c in current:
            c.diff = "new"
        return current, {"previous": None, "new": len(current), "changed": 0, "same": 0, "gone": 0}
    live = [p for p in previous if p.diff != "gone"]
    _rekey(current, live)
    prev_map = {p.key: p for p in live}
    counts: Counter = Counter()
    for c in current:
        p = prev_map.get(c.key)
        c.diff = "new" if p is None else ("changed" if _changed(c, p) else "same")
        counts[c.diff] += 1
    current_keys = {c.key for c in current}
    gone = [replace(p, diff="gone") for p in live if p.key not in current_keys]
    return current + gone, {
        "previous": previous_name, "new": counts["new"], "changed": counts["changed"],
        "same": counts["same"], "gone": len(gone),
    }
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_diff.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/diff.py test/test_insights_diff.py
git commit -m "feat(insights): diff findings against the previous snapshot"
```

---

### Task 15: Snapshot store

**Files:**
- Create: `artmind/insights/store.py`
- Test: `test/test_insights_store.py`

- [ ] **Step 1: Write the failing test**

```python
import json

import pytest

import paths
from artmind.insights import store
from artmind.insights.model import Finding, InsightsError, Run


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    return tmp_path


def make_run(created_at="2026-10-17T09:30:12Z", domain="demo"):
    return Run(domain=domain, created_at=created_at, as_of="2026-10-01", fingerprint={}, anchors=[],
               profile={}, plan=[], circles=[], diff={},
               findings=[Finding(key="k", detector="dormancy", kind="corpus", score=0.5, title="t")])


def test_write_creates_run_and_latest_without_temp_files(roots):
    path = store.write_run(make_run())
    assert path == roots / "insights" / "demo" / "runs" / "2026-10-17T09-30-12Z.json"
    assert json.loads(path.read_text())["format"] == 1
    assert (roots / "insights" / "demo" / "latest.json").read_text() == path.read_text()
    assert not list((roots / "insights").rglob("*.tmp"))
    assert store.load_latest("demo") == make_run()


def test_latest_missing_and_unreadable(roots):
    assert store.load_latest("demo") is None
    (roots / "insights" / "demo").mkdir(parents=True)
    (roots / "insights" / "demo" / "latest.json").write_text("{not json")
    with pytest.raises(InsightsError, match="unreadable"):
        store.load_latest("demo")


def test_list_and_prune_keep_the_newest(roots):
    for minute in range(5):
        store.write_run(make_run(created_at=f"2026-10-17T09:0{minute}:00Z"))
    assert len(store.list_runs("demo")) == 5
    removed = store.prune("demo", keep=2)
    assert removed == [f"2026-10-17T09-0{m}-00Z.json" for m in range(3)]
    assert [p.name for p in store.list_runs("demo")] == ["2026-10-17T09-03-00Z.json", "2026-10-17T09-04-00Z.json"]


def test_domain_names_cannot_escape(roots):
    with pytest.raises(InsightsError):
        store.domain_dir("../etc")
    assert store.domain_dir("finance.retail") == roots / "insights" / "finance.retail"
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_store.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/store.py`

```python
"""Insights snapshots on disk (spec §9.1-§9.2).

`<INSIGHTS_DIR>/<domain>/runs/<created_at>.json` plus `latest.json`. Writes are
atomic: the temp file goes in LOGS_DIR (gitignored, same filesystem), never in
the committed folder, as for review dossiers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from artmind.insights.model import InsightsError, Run

LATEST = "latest.json"
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def insights_root() -> Path:
    import paths

    return Path(paths.INSIGHTS_DIR)


def domain_dir(domain: str) -> Path:
    if not _DOMAIN_RE.match(domain):
        raise InsightsError(f"invalid domain name {domain!r}")
    return insights_root() / domain


def runs_dir(domain: str) -> Path:
    return domain_dir(domain) / "runs"


def run_filename(created_at: str) -> str:
    return created_at.replace(":", "-") + ".json"


def _write_atomic(path: Path, text: str) -> None:
    import paths

    path.parent.mkdir(parents=True, exist_ok=True)
    Path(paths.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".insights-", suffix=".tmp", dir=paths.LOGS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dumps(run: Run) -> str:
    return json.dumps(run.to_dict(), ensure_ascii=False, indent=1, default=str) + "\n"


def write_run(run: Run) -> Path:
    text = dumps(run)
    path = runs_dir(run.domain) / run_filename(run.created_at)
    _write_atomic(path, text)
    _write_atomic(domain_dir(run.domain) / LATEST, text)
    return path


def load_run(path: Path) -> Run:
    try:
        return Run.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InsightsError(f"{path} is unreadable: {exc}") from exc


def load_latest(domain: str) -> Run | None:
    path = domain_dir(domain) / LATEST
    return load_run(path) if path.is_file() else None


def list_runs(domain: str) -> list[Path]:
    root = runs_dir(domain)
    return sorted(root.glob("*.json")) if root.is_dir() else []


def prune(domain: str, keep: int) -> list[str]:
    runs = list_runs(domain)
    doomed = runs[:-keep] if keep > 0 and len(runs) > keep else []
    for path in doomed:
        path.unlink()
    return [p.name for p in doomed]
```

Note: `json.loads` raises `json.JSONDecodeError`, a subclass of `ValueError`, so the unreadable test is covered.

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_store.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/store.py test/test_insights_store.py
git commit -m "feat(insights): atomic snapshot store with latest pointer and retention"
```

---

### Task 16: Runner

**Files:**
- Create: `artmind/insights/runner.py`
- Test: `test/test_insights_runner.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

import paths
from _insights_helpers import FakeProjector
from artmind.insights import runner, store
from artmind.insights.model import InsightsError

SCHEMA = {"entity_types": {"PERSON": {"kind": "recurrent", "type_examples": ["self", "friend"]}}}


def _tables():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    rows = [{"_id": f"a{i}", "name": f"A{i}", "cls": "PERSON", "type": "friend", "days": da, "docs": 20} for i in range(1, 5)]
    rows += [{"_id": f"b{i}", "name": f"B{i}", "cls": "PERSON", "type": "friend", "days": db, "docs": 20} for i in range(1, 5)]
    rows.append({"_id": "x", "name": "X", "cls": "PERSON", "type": "friend",
                 "days": da[:3] + db[:3] + da[10:13] + db[10:13], "docs": 12})
    rows.append({"_id": "me", "name": "Me", "cls": "PERSON", "type": "self", "days": da + db, "docs": 40})
    days = sorted(set(da + db))
    docs = [{"id": f"d{i}", "valid_from": d, "time_source": "document_label"} for i, d in enumerate(days)]
    return {"entity_evidence": rows, "documents": docs, "property_stats": [], "relation_triples": [], "open_conflicts": []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", tmp_path / "cfg")
    monkeypatch.setattr(runner, "load_schema", lambda domain: SCHEMA)
    monkeypatch.setattr(runner, "_hashes", lambda domain: {"schema": "s", "same_as": "a", "config": "c"})
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "demo.yaml").write_text("min_period_docs: 5\n")
    return tmp_path


def refresh(**kw):
    return runner.refresh("demo", projector=FakeProjector(_tables()), now=kw.pop("now", "2026-10-17T09:00:00Z"), **kw)


def test_first_refresh_writes_a_snapshot(env):
    result = refresh()
    assert result["status"] == "written"
    assert result["anchors"] == [{"_id": "me", "name": "Me", "rule": "type=self"}]
    run = store.load_latest("demo")
    assert run.as_of == "2024-06-10"
    assert {f.diff for f in run.findings} == {"new"}
    assert {p.detector for p in run.plan if p.status == "ran"} >= {"communities", "brokers", "kind_mismatch"}
    assert "brokers:PERSON:x" in {f.key for f in run.findings}
    assert all(f.subjects == [] or "me" not in {s["_id"] for s in f.subjects} for f in run.findings)
    assert run.circles and run.circles[0]["name_source"] == "template"


def test_unchanged_graph_is_up_to_date_and_force_rewrites(env):
    refresh()
    assert refresh(now="2026-10-17T10:00:00Z")["status"] == "up_to_date"
    forced = refresh(now="2026-10-17T10:00:00Z", force=True)
    assert forced["status"] == "written"
    assert forced["diff"]["new"] == 0 and forced["diff"]["gone"] == 0
    assert len(store.list_runs("demo")) == 2


def test_only_runs_selected_detectors_and_carries_the_rest(env):
    refresh()
    before = {f.key for f in store.load_latest("demo").findings}
    result = refresh(now="2026-10-17T11:00:00Z", only=["kind_mismatch"])
    run = store.load_latest("demo")
    assert {f.key for f in run.findings if f.diff != "gone"} == before
    assert {p.detector for p in run.plan} == {"kind_mismatch"}
    assert result["status"] == "written"


def test_unknown_detector_and_empty_graph(env):
    with pytest.raises(InsightsError, match="unknown detector"):
        refresh(only=["nope"])
    with pytest.raises(InsightsError, match="no entities"):
        runner.refresh("demo", projector=FakeProjector({}), now="2026-10-17T09:00:00Z")


def test_same_rows_give_identical_findings(env):
    refresh(force=True)
    first = [{**f.to_dict(), "diff": None} for f in store.load_latest("demo").findings]
    first_profile = store.load_latest("demo").profile
    refresh(force=True)
    second = [{**f.to_dict(), "diff": None} for f in store.load_latest("demo").findings]
    assert first == second  # only the diff state (new -> same) may differ
    assert store.load_latest("demo").profile == first_profile


def test_retention_follows_config(env):
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\nkeep_runs: 1\n")
    refresh(force=True)
    result = refresh(now="2026-10-17T12:00:00Z", force=True)
    assert result["pruned"] == ["2026-10-17T09-00-00Z.json"]
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_runner.py -q`. Expected: FAIL, `ImportError`.

- [ ] **Step 3: Implement** `artmind/insights/runner.py`

```python
"""The insights pipeline (spec §5): profile -> plan -> project -> compute -> score -> diff -> snapshot.

Narration (spec §11) is phase 3. `refresh` short-circuits when the
fingerprint is unchanged, unless forced or limited to `only` detectors.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone

import networkx as nx
from neo4j.exceptions import DriverError, Neo4jError

from artmind.graph_query import expand_domain_family
from artmind.insights import store
from artmind.insights.config import config_hash, load_config
from artmind.insights.detectors import RunContext, Skip, registry
from artmind.insights.diff import apply_diff
from artmind.insights.model import Finding, InsightsError, PlanEntry, Run
from artmind.insights.profile import build_profile, infer_anchors
from artmind.insights.projections import Projector
from artmind.temporal import load_schema


def _hashes(domain: str) -> dict:
    from artmind import projection, same_as

    return {"schema": projection.schema_set_hash(), "same_as": same_as.content_hash(), "config": config_hash(domain)}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_detectors(ctx: RunContext, selected: set[str] | None) -> tuple[list[Finding], list[PlanEntry]]:
    findings: list[Finding] = []
    plan: list[PlanEntry] = []
    for det in registry():
        if selected is not None and det.name not in selected:
            continue
        if not ctx.config.enabled(det.name):
            plan.append(PlanEntry(det.name, "skipped", reason="disabled in config"))
            continue
        targets = det.plan(ctx)
        if isinstance(targets, Skip):
            plan.append(PlanEntry(det.name, "skipped", reason=targets.reason))
            continue
        for target in targets:
            try:
                found = det.run(ctx, target)
            except (InsightsError, nx.NetworkXError) as exc:
                plan.append(PlanEntry(det.name, "failed", target=target, reason=str(exc)))
                continue
            plan.append(PlanEntry(det.name, "ran", target=target, findings=len(found)))
            findings.extend(found)
    return findings, plan


def _trim(findings: list[Finding], max_per_detector: int) -> list[Finding]:
    order = [d.name for d in registry()]
    by_detector: dict[str, list[Finding]] = {}
    for f in findings:
        by_detector.setdefault(f.detector, []).append(f)
    out = []
    for name in order:
        ranked = sorted(by_detector.get(name, []), key=lambda f: (-f.score, f.key))
        out.extend(ranked[:max_per_detector])
    return out


def refresh(domain: str, *, force: bool = False, only: list[str] | None = None,
            projector=None, now: str | None = None) -> dict:
    config = load_config(domain)
    selected = set(only) if only else None
    if selected:
        unknown = selected - {d.name for d in registry()}
        if unknown:
            raise InsightsError(f"unknown detector(s): {sorted(unknown)}")
    schema = load_schema(domain)
    try:
        projector = projector or Projector(expand_domain_family(domain))
        profile, entities = build_profile(projector, schema, config)
    except (Neo4jError, DriverError) as exc:
        raise InsightsError(f"graph unavailable: {getattr(exc, 'message', None) or exc}") from exc
    if not entities:
        raise InsightsError(f"domain {domain!r} has no entities in the graph")
    anchors = infer_anchors(entities, config, profile.dated_days)
    fingerprint = {**_hashes(domain), "entities": len(entities), "latest_doc": profile.latest_doc}

    previous = store.load_latest(domain)
    if previous and previous.fingerprint == fingerprint and not force and selected is None:
        return {"status": "up_to_date", "domain": domain, "created_at": previous.created_at, "as_of": previous.as_of}

    ctx = RunContext(domain=domain, schema=schema, config=config, profile=profile, entities=entities,
                     anchors={a["_id"] for a in anchors}, as_of=profile.latest_doc)
    found, plan = run_detectors(ctx, selected)
    found = _trim(found, config.max_findings)

    prev_findings, carried = None, []
    if previous is not None:
        prev_findings = [f for f in previous.findings if selected is None or f.detector in selected]
        if selected is not None:
            carried = [replace(f, diff="same") for f in previous.findings
                       if f.detector not in selected and f.diff != "gone"]
    runs = store.list_runs(domain)
    findings, diff = apply_diff(found, prev_findings, runs[-1].name if previous is not None and runs else None)

    circles = [c for key, (_g, cs) in sorted(ctx.memo.items(), key=lambda kv: str(kv[0]))
               if isinstance(key, tuple) and key[0] == "circles" for c in cs]
    if selected is not None and "communities" not in selected and previous is not None:
        circles = previous.circles

    run = Run(domain=domain, created_at=now or _now(), as_of=profile.latest_doc, fingerprint=fingerprint,
              anchors=anchors, profile=profile.to_dict(), plan=plan, findings=findings + carried,
              circles=circles, diff=diff)
    path = store.write_run(run)
    pruned = store.prune(domain, config.keep_runs)
    return {
        "status": "written", "domain": domain, "path": str(path), "as_of": run.as_of, "anchors": anchors,
        "findings": dict(sorted(Counter(f.detector for f in run.findings if f.diff != "gone").items())),
        "diff": diff, "skipped": [p.to_dict() for p in plan if p.status != "ran"], "pruned": pruned,
    }
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run --group dev pytest test/test_insights_runner.py -q`. Expected: all PASS.

If `test_same_rows_give_identical_findings` fails, the cause is ordering: some dict or list is built from a set or unsorted source. Fix it in the producing module (sort it); do not loosen the test.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/runner.py test/test_insights_runner.py
git commit -m "feat(insights): refresh pipeline with fingerprint short-circuit, diff, trim and retention"
```

---

### Task 17: CLI

**Files:**
- Modify: `artmind/cli.py`:
  - add `"insights"` to the `"Query"` entry of `click.rich_click.COMMAND_GROUPS["artmind"]` (line ~226);
  - add the new group directly before the `# ── artmind traces` banner (line ~3318).
- Test: `test/test_insights_cli.py`

- [ ] **Step 1: Write the failing test**

```python
import json

import pytest
from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.insights import store
from artmind.insights.model import Finding, Run


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    findings = [
        Finding(key="dormancy:PERSON:p1", detector="dormancy", kind="corpus", score=0.9, title="Quiet: Asha", diff="new"),
        Finding(key="kind_mismatch:ACTIVITY", detector="kind_mismatch", kind="health", score=0.4,
                title="ACTIVITY mismatch", action="schema", diff="same"),
        Finding(key="dormancy:PERSON:p9", detector="dormancy", kind="corpus", score=0.2, title="Quiet: Ben", diff="gone"),
    ]
    run = Run(domain="demo", created_at="2026-10-17T09:00:00Z", as_of="2026-10-01", fingerprint={}, anchors=[],
              profile={}, plan=[], circles=[], diff={"new": 1}, findings=findings)
    store.write_run(run)
    return run


def invoke(*args):
    return CliRunner().invoke(cli, ["insights", *args, "--compact"])


def test_list_ranks_live_findings_and_filters(snap):
    out = json.loads(invoke("list", "--domain", "demo").output)
    assert [f["key"] for f in out["findings"]] == ["dormancy:PERSON:p1", "kind_mismatch:ACTIVITY"]
    assert out["as_of"] == "2026-10-01"
    health = json.loads(invoke("list", "--domain", "demo", "--kind", "health").output)
    assert [f["key"] for f in health["findings"]] == ["kind_mismatch:ACTIVITY"]
    changed = json.loads(invoke("list", "--domain", "demo", "--new").output)
    assert [f["key"] for f in changed["findings"]] == ["dormancy:PERSON:p1", "dormancy:PERSON:p9"]


def test_show_and_missing_key(snap):
    out = json.loads(invoke("show", "dormancy:PERSON:p1", "--domain", "demo").output)
    assert out["finding"]["title"] == "Quiet: Asha" and out["created_at"] == "2026-10-17T09:00:00Z"
    missing = invoke("show", "nope", "--domain", "demo")
    assert missing.exit_code != 0 and "no finding" in missing.output


def test_runs_lists_history(snap):
    out = json.loads(invoke("runs", "--domain", "demo").output)
    assert out["runs"] == [{"run": "2026-10-17T09-00-00Z.json", "created_at": "2026-10-17T09:00:00Z",
                            "as_of": "2026-10-01", "findings": 2, "diff": {"new": 1}}]


def test_no_snapshot_is_a_friendly_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "empty")
    result = invoke("list", "--domain", "demo")
    assert result.exit_code != 0 and "no insights snapshot" in result.output


def test_refresh_passes_options_through(monkeypatch):
    from artmind.insights import runner

    seen = {}
    monkeypatch.setattr(runner, "refresh", lambda domain, **kw: seen.update(domain=domain, **kw) or {"status": "written"})
    result = invoke("refresh", "--domain", "demo", "--force", "--only", "dormancy, brokers")
    assert result.exit_code == 0, result.output
    assert seen == {"domain": "demo", "force": True, "only": ["dormancy", "brokers"]}


def test_refresh_needs_exactly_one_domain():
    result = invoke("refresh", "--domain", "a,b")
    assert result.exit_code != 0 and "exactly one --domain" in result.output
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run --group dev pytest test/test_insights_cli.py test/test_cli_guide.py -q`

Expected: `test_insights_cli.py` FAILS with "No such command 'insights'"; `test_cli_guide.py` passes.

- [ ] **Step 3: Implement**

In `artmind/cli.py`, change the `"Query"` entry of `COMMAND_GROUPS["artmind"]` to:

```python
        {"name": "Query", "commands": ["query", "views", "insights", "traces", "review"]},
```

Insert this block directly before the `# ── artmind traces ───` banner:

```python
# ── artmind insights ───────────────────────────────────────────────────────────


@cli.group("insights")
def insights_group() -> None:
    """Unprompted findings over a domain's graph: trends, quiet entities, circles, brokers, graph health.

    `refresh` profiles the graph against its schema, runs every detector that applies and
    writes a diffed snapshot to `<ARTMIND_HOME>/insights/<domain>/` (committed with a vault).
    `list`, `show` and `runs` read snapshots only (no Neo4j). Tune detectors in
    `domains/insights/<domain>.yaml`. Not proxied by `serve`.
    """
    pass


def _one_domain(domain: tuple) -> str:
    domains = _parse_domains(domain)
    if len(domains) != 1:
        raise click.UsageError("insights run per domain: give exactly one --domain")
    return domains[0]


def _latest_insights(domain: str):
    from artmind.insights import store as insights_store
    from artmind.insights.model import InsightsError

    try:
        run = insights_store.load_latest(domain)
    except InsightsError as exc:
        raise click.ClickException(str(exc)) from exc
    if run is None:
        raise click.ClickException(
            f"no insights snapshot for {domain}: run `artmind insights refresh --domain {domain}`"
        )
    return run


@insights_group.command("refresh")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain to analyse (one)")
@click.option("--force", is_flag=True, help="Run even if graph, schemas, curation and config are unchanged")
@click.option("--only", "only", default=None, help="Comma-separated detectors; other findings are carried over")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_refresh(domain: tuple, force: bool, only: str | None, compact: bool) -> None:
    """Profile the graph, run the detectors and write a snapshot. Needs Neo4j."""
    from artmind.insights import runner as insights_runner
    from artmind.insights.model import InsightsError

    selected = [s.strip() for s in only.split(",") if s.strip()] if only else None
    name = _one_domain(domain)
    try:
        result = insights_runner.refresh(name, force=force, only=selected)
    except InsightsError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@insights_group.command("list")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--detector", default=None, help="Only this detector")
@click.option("--kind", type=click.Choice(["corpus", "health"]), default=None, help="Corpus findings or graph health")
@click.option("--new", "only_new", is_flag=True, help="Only what changed since the previous run (new, changed, gone)")
@click.option("--limit", default=50, show_default=True, type=int, help="Maximum findings")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_list(domain: tuple, detector: str | None, kind: str | None, only_new: bool, limit: int, compact: bool) -> None:
    """Findings from the latest snapshot, highest score first. No Neo4j."""
    name = _one_domain(domain)
    run = _latest_insights(name)
    wanted = {"new", "changed", "gone"} if only_new else {"new", "changed", "same"}
    items = [
        f for f in run.findings
        if f.diff in wanted and (detector is None or f.detector == detector) and (kind is None or f.kind == kind)
    ]
    items.sort(key=lambda f: (-f.score, f.key))
    _echo_json({
        "domain": name, "created_at": run.created_at, "as_of": run.as_of, "anchors": run.anchors, "diff": run.diff,
        "findings": [
            {"key": f.key, "detector": f.detector, "kind": f.kind, "score": f.score, "title": f.title,
             "diff": f.diff, "action": f.action}
            for f in items[:limit]
        ],
    }, compact)


@insights_group.command("show")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("key")
def insights_show(domain: tuple, compact: bool, key: str) -> None:
    """One finding with its full evidence and card. No Neo4j."""
    name = _one_domain(domain)
    run = _latest_insights(name)
    match = next((f for f in run.findings if f.key == key), None)
    if match is None:
        raise click.ClickException(f"no finding {key!r} in the latest {name} snapshot")
    _echo_json({"domain": name, "created_at": run.created_at, "as_of": run.as_of, "finding": match.to_dict()}, compact)


@insights_group.command("profile")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_profile(domain: tuple, compact: bool) -> None:
    """The graph's shape profile and inferred anchors. Needs Neo4j; writes nothing."""
    from neo4j.exceptions import DriverError, Neo4jError

    from artmind.insights.config import load_config
    from artmind.insights.model import InsightsError
    from artmind.insights.profile import build_profile, infer_anchors
    from artmind.insights.projections import Projector
    from artmind.temporal import load_schema

    name = _one_domain(domain)
    try:
        config = load_config(name)
        profile, entities = build_profile(Projector(graph_query.expand_domain_family(name)), load_schema(name), config)
        anchors = infer_anchors(entities, config, profile.dated_days)
    except (InsightsError, Neo4jError, DriverError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json({"domain": name, "entities": len(entities), "anchors": anchors, "profile": profile.to_dict()}, compact)


@insights_group.command("runs")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_runs(domain: tuple, compact: bool) -> None:
    """Snapshot history with diff counts. No Neo4j."""
    from artmind.insights import store as insights_store
    from artmind.insights.model import InsightsError

    name = _one_domain(domain)
    rows = []
    try:
        for path in insights_store.list_runs(name):
            run = insights_store.load_run(path)
            rows.append({"run": path.name, "created_at": run.created_at, "as_of": run.as_of,
                         "findings": sum(1 for f in run.findings if f.diff != "gone"), "diff": run.diff})
    except InsightsError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json({"domain": name, "runs": rows}, compact)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run --group dev pytest test/test_insights_cli.py test/test_cli_guide.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_insights_cli.py
git commit -m "feat(insights): artmind insights refresh/list/show/profile/runs"
```

---

### Task 18: Docs, recipes, spec alignment and end-to-end check

**Files:**
- Modify: `CLAUDE.md` (repo-layout table and command routing)
- Modify: `justfile` (after the `views-test` recipe)
- Modify: `docs/superpowers/specs/2026-10-17-insights-layer-design.md` (§5 step 5, §6.1 score)

- [ ] **Step 1: Update `CLAUDE.md`**

Add this row to the repo-layout table, directly after the `artmind/kg_views/` row:

```markdown
| `artmind/insights/` | Unprompted findings over a domain's graph (spec `docs/superpowers/specs/2026-10-17-insights-layer-design.md`). `projections` (named read-only `$domains`-scoped Cypher, memoised), `profile` (shape profile, effective kind, anchors), `detectors/` (salience/type trends, dormancy, communities, brokers, graph health; pure Python + networkx, no numpy), `diff`, `store` (snapshots at `paths.INSIGHTS_DIR`, `<vault>/.artmind/insights/<domain>/`, committed; atomic writes via `logs/`), `runner`. Tuning: `domains/insights/<domain>.yaml`, never seeded, outside `schemas/` so it never moves `schema_set_hash()`. **Snapshots hold private corpus data** — never copy them into code, tests or commits. CLI: `artmind insights refresh/list/show/profile/runs` (not proxied). |
```

In "Command routing quick reference", after the `artmind views <cmd>` bullet, add:

```markdown
- `artmind insights <cmd>` — `refresh` (needs Neo4j), `list`, `show`, `profile`, `runs` (not proxied)
```

- [ ] **Step 2: Add `justfile` recipes** after the `views-test` recipe

```make
# ── artmind insights ─────────────────────────────────────────────────────────

# profile the graph, run detectors, write a snapshot  (usage: just insights-refresh <domain> [--force])
insights-refresh domain *flags:
    uv run artmind insights refresh --domain {{ domain }} {{ flags }}

# findings from the latest snapshot  (usage: just insights-list <domain>)
insights-list domain:
    uv run artmind insights list --domain {{ domain }}
```

- [ ] **Step 3: Align the spec with two decisions made while planning**

In `docs/superpowers/specs/2026-10-17-insights-layer-design.md`:

- §6.1: change the score bullet to read: `**Score:** min(1, |log2 ratio| / 3 · min(1, sqrt(support / 30)))`. An 8× change at full support scores 1.0.
- §5 step 5: change "keeps the top `max_findings` per detector (default 25) plus every finding the diff marks new" to "keeps the top `max_findings` per detector (default 25)". Then add one sentence: "A `--only` refresh carries the other detectors' findings over unchanged, so they are not reported as gone."

- [ ] **Step 4: Run every insights test plus the touched suites**

Run: `uv run --group dev pytest test/test_insights_*.py test/test_cli_guide.py test/test_scaffold_run_folder.py test/test_vault_scaffold.py test/test_kg_views_layout.py -q`

Expected: all PASS.

- [ ] **Step 5: End-to-end against a real vault** (outside the hermetic suite; Neo4j must be running)

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind insights refresh --domain personal_journal --compact
```

Expected: `"status":"written"`; `anchors` names the diarist with rule `type=self`; `skipped` lists only detectors with a stated reason.

Then check that a second run is up to date:

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind insights refresh --domain personal_journal --compact
```

Expected: `"status":"up_to_date"`.

Then look at the findings, and **do not paste their content anywhere in the repo**:

```bash
cd ~/artmind_vaults/brain01 && artmind insights list --domain personal_journal --limit 20
```

Expected:
- `communities` circles;
- `brokers`;
- `kind_mismatch` for ACTIVITY;
- `vocabulary_sprawl` for PLAN.status and EMOTION.type;
- `relationship_usage` overlap for WENT_TO/VISITS;
- `dormancy` and `salience_trend` findings.

If a projection errors on the live server (for example `valueType` on an older Neo4j), fix the Cypher in `projections.py` and add a recorder test pinning the new text.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md justfile docs/superpowers/specs/2026-10-17-insights-layer-design.md
git commit -m "docs(insights): repo layout, routing, just recipes; align spec scoring and trimming"
```

---

## Spec requirements already met by existing code

- §9.1 "ingest must refuse paths under `.artmind/`" already holds: `artmind/ingest.py:123` skips every path with a dot-prefixed part, and `vault.yaml` only ingests mapped folders. No task is needed.

## Out of scope for phase 1 (later plans)

These are spec §15 phases 2–4:
- `outcome_lift`, `same_as_candidates` (+ export), `split_signal`, `transitions`, `open_loops`, `attribution_suspects`, `fragments`;
- narration and circle naming;
- `insights config check`;
- `query insights` plus the `artmind-query` section, and the `artmind-insights` skill;
- the admin-ui Insights and Views tabs;
- leakcheck reading snapshot subject names.
