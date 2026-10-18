# Insights layer — phase 4 (agent route, skills, admin-ui Insights and Views tabs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make insights usable where people actually work:
- the chat agent reads them through `artmind query insights list|show`, guided by an Insights section in `artmind-query`;
- operators get an `artmind-insights` skill;
- the admin console gains an **Insights** tab (cards, a circle network, refresh, card actions) and a read-only **Views** tab.

**Architecture:**
- **Query route.** `query insights` sits under the existing `TracedGroup` `query` group, so the `serve` daemon proxies it and traces record it with no extra wiring. It reads `latest.json` only.
- **Shared snapshot code.** A small `artmind/insights/listing.py` holds the code `artmind insights list/show` and `query insights` share.
- **Shared merge-building.** `artmind/insights/sameas_merges.py` takes the guarded merge-building out of the `insights same-as` CLI, so the admin console's "Send to curation" uses identical rules.
- **Routes.** `artmind/webui/insights_routes.py` holds the Insights and Views JSON routes. It is registered next to the dashboard routes.
- **Frontend.** Plain JS in `static/insights.js` and `static/views.js` (the dashboard's existing no-framework style), with a ~100-line SVG force layout. Every snapshot string is inserted with `textContent`, never `innerHTML` (spec §11 rendering rule).

**Tech Stack:** Python ≥3.14, Click, FastAPI (`TestClient` for tests), vanilla JS/CSS, the vendored `marked` + `purify` (view markdown only), pytest.

Spec: `docs/superpowers/specs/2026-10-17-insights-layer-design.md` §10.2–§10.5; phase 4 = §15 item 4.

**Decisions recorded here:**
- **No "graph has newer documents" badge.** The query CLI already prints the "graph is N docs behind" staleness line, and the tab shows `as_of`/`created_at`.
- **"Ask in console"** opens `/?ask=<question>`. The console pre-fills the input and never auto-sends.
- **"Send to curation"** builds merges with the CLI's guards. It then calls `refine_graph.propose_merges`, which writes same-as *proposals* (the review queue), never merges.
- **Insights is the default "summary" surface**, so its tab goes right after "Ingest & browse". Views goes next to Schemas.

---

## Conventions for every task

- Run only the targeted test files with `uv run --group dev pytest <files> -q`. Never run the full suite.
- Tests are hermetic and synthetic. Never put credential literals in tests (see the repo memory on Block Secrets).
- Commit only your own paths: `git add <new>` then `git commit -m "<msg>" -- <paths>`. Never use `-a`/`-A`. Retry on `index.lock`.
- End every commit with the Co-Authored-By trailer your system reminder specifies.
- Branch: `feat/insights-phase4`.
- A running `admin-ui` serves stale code, so restart it after edits.

---

### Task 1: Shared listing, `query insights`, shared same-as merges

**Files:**
- Create: `artmind/insights/listing.py`
- Create: `artmind/insights/sameas_merges.py`
- Modify: `artmind/cli.py`:
  - `insights list/show/same-as` use the shared modules;
  - add the `query insights` group under `query`;
  - add `"insights"` to `COMMAND_GROUPS["artmind query"]`'s "Saved views" entry, renamed to "Saved views & insights".
- Test: `test/test_insights_listing.py` (new), `test/test_insights_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

`test/test_insights_listing.py`:

```python
from artmind.insights.listing import finding_summary, list_findings
from artmind.insights.model import Finding, Run
from artmind.insights.sameas_merges import build_merges


def F(key, score, diff="same", detector="dormancy", kind="corpus", **kw):
    return Finding(key=key, detector=detector, kind=kind, score=score, title=key, diff=diff, **kw)


def run(findings):
    return Run(domain="demo", created_at="t", as_of="2026-10-01", fingerprint={}, anchors=[], profile={},
               plan=[], circles=[], diff={}, findings=findings)


def test_list_filters_and_ranks():
    r = run([F("a", 0.2), F("b", 0.9), F("g", 0.99, diff="gone"), F("h", 0.5, kind="health", detector="kind_mismatch")])
    assert [f.key for f in list_findings(r)] == ["b", "h", "a"]
    assert [f.key for f in list_findings(r, kind="health")] == ["h"]
    assert [f.key for f in list_findings(r, detector="dormancy", limit=1)] == ["b"]
    assert [f.key for f in list_findings(r, only_new=True)] == ["g"]


def test_summary_carries_narration_and_subject_ids():
    f = F("a", 0.5, narration="A has gone quiet.", subjects=[{"_id": "p1", "name": "A", "class": "PERSON"}])
    s = finding_summary(f)
    assert s["narration"] == "A has gone quiet." and s["subjects"] == [{"_id": "p1", "name": "A", "class": "PERSON"}]
    assert set(s) == {"key", "detector", "kind", "score", "title", "narration", "diff", "action", "subjects"}


def cand(a, b, score, aid=None, bid=None):
    return Finding(key=f"same_as_candidates:PERSON:{a}:{b}", detector="same_as_candidates", kind="health",
                   score=score, title="t", action="curate",
                   stats={"canonical": {"_id": aid or a, "name": a, "class": "PERSON"},
                          "alias": {"_id": bid or b, "name": b, "class": "PERSON"}, "rule": "kinship"})


def test_build_merges_guards_chains_and_ambiguous_names():
    merges, candidates, skipped = build_merges([cand("Dad", "Daddy", 0.9), cand("Daddy", "Da", 0.8),
                                                cand("Sam", "S", 0.7, bid="s1"), cand("Lee", "S", 0.6, bid="s2")])
    assert merges == {"Daddy": "Dad"} and skipped == {"chain": 1, "ambiguous_name": 2}
    assert candidates[0]["alias"] == "Daddy"
```

Append to `test/test_insights_cli.py`. The `snap` fixture already writes a run with findings `dormancy:PERSON:p1` (new), `kind_mismatch:ACTIVITY` (same) and `dormancy:PERSON:p9` (gone):

```python
def test_query_insights_list_and_show(snap):
    out = json.loads(CliRunner().invoke(cli, ["query", "insights", "list", "--domain", "demo", "--compact"]).output)
    assert out["as_of"] == "2026-10-01" and out["created_at"] == "2026-10-17T09:00:00Z"
    assert [f["key"] for f in out["findings"]] == ["dormancy:PERSON:p1", "kind_mismatch:ACTIVITY"]
    assert "circles" in out
    shown = json.loads(CliRunner().invoke(cli, ["query", "insights", "show", "dormancy:PERSON:p1",
                                                "--domain", "demo", "--compact"]).output)
    assert shown["finding"]["title"] == "Quiet: Asha"


def test_query_insights_without_snapshot_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "none")
    r = CliRunner().invoke(cli, ["query", "insights", "list", "--domain", "demo", "--compact"])
    assert r.exit_code != 0 and "no insights snapshot" in r.output
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_listing.py test/test_insights_cli.py test/test_cli_guide.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/listing.py`:

```python
"""Reading a snapshot for people and agents: shared by `artmind insights list/show` and `query insights`."""

from __future__ import annotations

LIVE = frozenset({"new", "changed", "same"})
CHANGED = frozenset({"new", "changed", "gone"})


def list_findings(run, *, detector: str | None = None, kind: str | None = None,
                  only_new: bool = False, limit: int | None = None) -> list:
    wanted = CHANGED if only_new else LIVE
    items = [f for f in run.findings
             if f.diff in wanted and (detector is None or f.detector == detector) and (kind is None or f.kind == kind)]
    items.sort(key=lambda f: (-f.score, f.key))
    return items[:limit] if limit else items


def finding_summary(f) -> dict:
    return {"key": f.key, "detector": f.detector, "kind": f.kind, "score": f.score, "title": f.title,
            "narration": f.narration, "diff": f.diff, "action": f.action,
            "subjects": [{"_id": s.get("_id"), "name": s.get("name"), "class": s.get("class")} for s in f.subjects]}


def circle_summary(c: dict) -> dict:
    return {"id": c.get("id"), "name": c.get("name"), "name_source": c.get("name_source"),
            "size": len(c.get("members", []))}
```

`artmind/insights/sameas_merges.py`: move the body of `insights_same_as`'s merge loop here, unchanged:

```python
"""Guarded same-as merges from same_as_candidates findings, shared by the CLI export and the admin console."""

from __future__ import annotations

NOTE = ("refine-graph resolves names to entities; its stats (skipped_ambiguous, skipped_cross_class) are "
        "the final word. This file holds names from your vault: keep it out of the code checkout.")


def build_merges(findings, min_score: float = 0.0) -> tuple[dict[str, str], list[dict], dict[str, int]]:
    """{alias: canonical} by score; skips chains and names shared by two entities."""
    items = sorted((f for f in findings if f.detector == "same_as_candidates" and f.diff != "gone"
                    and f.score >= min_score), key=lambda f: (-f.score, f.key))
    ids_by_name: dict[str, set] = {}
    for f in items:
        for side in ("alias", "canonical"):
            ids_by_name.setdefault(f.stats[side]["name"], set()).add(f.stats[side].get("_id"))
    merges: dict[str, str] = {}
    candidates: list[dict] = []
    skipped = {"chain": 0, "ambiguous_name": 0}
    for f in items:
        alias, canonical = f.stats["alias"]["name"], f.stats["canonical"]["name"]
        if alias == canonical or alias in merges:
            continue
        if len(ids_by_name[alias]) > 1 or len(ids_by_name[canonical]) > 1:
            skipped["ambiguous_name"] += 1
            continue
        if alias in merges.values() or canonical in merges:
            skipped["chain"] += 1
            continue
        merges[alias] = canonical
        candidates.append({"alias": alias, "canonical": canonical, "class": f.stats["canonical"]["class"],
                           "score": f.score, "rule": f.stats.get("rule")})
    return merges, candidates, skipped
```

The `build_merges` test expects `skipped == {"chain": 1, "ambiguous_name": 2}`:
- "S" is an alias for two `_id`s (s1, s2), so both of its pairs count as ambiguous;
- `Daddy→Da` is a chain, because Daddy is already an alias.

Keep the loop as above. If the existing CLI tests disagree, the existing behaviour wins; report it.

`artmind/cli.py`:
1. **Shared code in the existing commands.** `insights_list` uses `list_findings` and `finding_summary`. `insights_same_as` uses `build_merges(run.findings, min_score)` and `NOTE`, and keeps its output identical. Existing CLI tests must still pass.
2. **`query insights` group.** Add it after the `query views` commands:

```python
@query.group("insights")
def query_insights() -> None:
    """Unprompted findings from the latest insights snapshot: what changed, trends, circles, associations.

    Read-only and fast (no graph query): `list` the top findings for a domain, `show` one with its
    evidence. Ground every named subject with `query entity-context` using its _id. Snapshots are
    written by `artmind insights refresh`; `as_of` says how fresh they are.
    """
    pass


@query_insights.command("list")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--detector", default=None, help="Only this detector (e.g. outcome_lift, transitions)")
@click.option("--kind", type=click.Choice(["corpus", "health"]), default=None)
@click.option("--new", "only_new", is_flag=True, help="Only what changed since the previous run")
@click.option("--limit", default=25, show_default=True, type=click.IntRange(1))
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def query_insights_list(domain: tuple, detector: str | None, kind: str | None, only_new: bool, limit: int,
                        compact: bool) -> None:
    """Top findings by score, with captions and the subjects' _ids for grounding."""
    from artmind.insights.listing import circle_summary, finding_summary, list_findings

    name = _one_domain(domain)
    run = _latest_insights(name)
    _echo_json({"domain": name, "as_of": run.as_of, "created_at": run.created_at,
                "circles": [circle_summary(c) for c in run.circles],
                "findings": [finding_summary(f) for f in list_findings(run, detector=detector, kind=kind,
                                                                     only_new=only_new, limit=limit)]}, compact)


@query_insights.command("show")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("key")
def query_insights_show(domain: tuple, compact: bool, key: str) -> None:
    """One finding with its full stats and card (the evidence behind a caption)."""
    name = _one_domain(domain)
    run = _latest_insights(name)
    match = next((f for f in run.findings if f.key == key), None)
    if match is None:
        raise click.ClickException(f"no finding {key!r} in the latest {name} snapshot")
    _echo_json({"domain": name, "as_of": run.as_of, "created_at": run.created_at, "finding": match.to_dict()}, compact)
```

`_one_domain` and `_latest_insights` are defined later in the file than the `query` group. Python resolves them at call time, so the order is fine.

3. **Help text.** Update the `query` group docstring's Groups line to mention `insights`. Update `COMMAND_GROUPS["artmind query"]`: `{"name": "Saved views & insights", "commands": ["views", "insights"]}`.

- [ ] **Step 4: Run the tests and see them pass**

Run the Step 2 command. Expected: all PASS. Then smoke test: `uv run artmind query insights --help`.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/listing.py artmind/insights/sameas_merges.py test/test_insights_listing.py
git commit -m "feat(insights): query insights list/show for agents; shared listing and same-as merges" -- artmind/insights/listing.py artmind/insights/sameas_merges.py artmind/cli.py test/test_insights_listing.py test/test_insights_cli.py
```

---

### Task 2: Skills

**Files:**
- Modify: `artmind/skills/artmind-query/SKILL.md` (a new `## Insights` section placed **before** `## The Query Protocol`; the protocol and its headings are unchanged)
- Create: `artmind/skills/artmind-insights/SKILL.md`
- Test: `test/test_insights_skills.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "artmind" / "skills"


def flat(text):
    return " ".join(text.split())


def test_query_skill_insights_section():
    skill = (SKILLS / "artmind-query" / "SKILL.md").read_text()
    assert skill.index("## Insights") < skill.index("## The Query Protocol")
    section = flat(skill.split("## Insights", 1)[1].split("## The Query Protocol", 1)[0])
    for needle in ("artmind query insights list --domain", "artmind query insights show",
                   "query entity-context", "Co-occurrence on the same day, not cause", "as_of",
                   "never run `artmind insights refresh`"):
        assert needle in section, needle


def test_insights_operator_skill():
    text = (SKILLS / "artmind-insights" / "SKILL.md").read_text()
    assert text.startswith("---\nname: artmind-insights\ndescription: ")
    body = flat(text)
    for needle in ("artmind insights refresh", "artmind insights profile", "artmind insights config check",
                   "domains/insights/<domain>.yaml", "artmind insights same-as --export",
                   "artmind-create-schema", "artmind-curate", "artmind-create-view", "source_question",
                   "--no-narrate"):
        assert needle in body, needle
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_skills.py test/test_query_skill_views_step.py -q`. Expected: FAIL.

- [ ] **Step 3: Write the skills**

Insert this section into `artmind-query/SKILL.md` immediately before `## The Query Protocol`:

````markdown
## Insights — for broad or retrospective questions

Some questions are about patterns rather than facts. Examples:
- "what's changed this year";
- "who matters most at work now";
- "what tends to go with bad days";
- "who have I lost touch with".

The domain owner's insights snapshot already answers those. Check it before walking the protocol below:

```bash
artmind query insights list --domain <domain> --compact
```

Each finding has a `title`, a `narration` (a checked one-line caption, or null) and its `subjects` with their `_id`s.
- Use `--detector` to focus: for example `outcome_lift` for associations, `transitions` for life changes, `dormancy` for people gone quiet, or `communities` for circles.
- Use `--new` for "what changed".
- Use `artmind query insights show <key> --domain <domain> --compact` for the numbers behind a finding.

When you answer from insights:
- **Ground each subject** you name with `artmind query entity-context --domain <domain> --entityId <_id> --compact`, and cite as usual.
- **outcome_lift findings** always carry the caption "Co-occurrence on the same day, not cause." Pass it on. Never describe a lift as a cause.
- **Freshness:** state the snapshot's `as_of`. If an `artmind:` stderr line says the graph is behind the vault, say that the insights may be older than the latest notes.
- **Refresh:** never run `artmind insights refresh` yourself. If there is no snapshot, say so; the domain owner runs it.
````

Make sure the `entity-context` flags match the real command: check `artmind query entity-context --help` and use its actual option names.

`artmind/skills/artmind-insights/SKILL.md`:

````markdown
---
name: artmind-insights
description: Operate the artmind insights layer for a domain. Covers running and reading `artmind insights refresh`, tuning `domains/insights/<domain>.yaml`, and turning findings into action: schema edits, same-as curation, or saved views. Use for "refresh insights", "what did insights find", "why is this finding here", "tune the insights config", or "promote this insight to a view". Not for answering end-user questions; artmind-query reads insights for that.
---

# artmind insights (operator)

Insights are unprompted findings computed from a domain's graph and its schema, written to a snapshot in
`<vault>/.artmind/insights/<domain>/`, committed with the vault and private. Each refresh diffs against the
previous snapshot.

## Run and read

```bash
artmind insights refresh --domain <d> --compact      # needs Neo4j; prints counts, diff, skipped detectors and llm stats
artmind insights list --domain <d> [--detector X] [--kind corpus|health] [--new]
artmind insights show <key> --domain <d>
artmind insights runs --domain <d>
```

- **`up_to_date`** means the graph, schemas, curation, config and detector version are unchanged. Use `--force` to recompute anyway.
- **Skipped detectors list a reason.** "not configured" means a config block is missing (see Tune). Data reasons, such as "no document dates", are expected on some corpora.
- **Captions and circle names** come from the configured LLM. `--no-narrate` skips them. They are checked: a caption names only the finding's own subjects and uses only numbers in its stats. Prompts send entity names to the configured provider.

## Tune

```bash
artmind insights profile --domain <d> --compact      # class kinds, property vocabularies, relations, anchors
artmind insights config check --domain <d>           # errors exit 1; warnings name values never seen
```

Edit `domains/insights/<domain>.yaml`. Every key is optional:
- `anchors`, if the inferred anchor is wrong;
- `period`;
- `outcome`, which enables outcome_lift: a class, its property (default `type`), and positive and negative values;
- `open_loops`, which enables open_loops: a class, status property, and open and closed values;
- `transitions` (`extra_verbs`, `classes`);
- per-detector settings under `detectors:`.

Draft `outcome` and `open_loops` values from `insights profile`, which lists each property's vocabulary. Then run `config check`.

## Act on findings

- **Schema (kind_mismatch, vocabulary_sprawl, relationship_usage).** Propose the schema edit and hand off to **artmind-create-schema**. Examples:
  - change a class's `kind`;
  - list allowed values in a property hint;
  - fold synonym relationship types.

  Afterwards, `artmind ingest retime --redoProperties` refolds properties without the LLM.
- **Identity (same_as_candidates, split_signal).** Run:
  ```bash
  artmind insights same-as --domain <d> --export <path outside the checkout>
  artmind ingest refine-graph --domain <d> --from-file <path>
  ```
  This writes *proposals* to the same-as review queue. Review them with **artmind-curate**; nothing merges automatically. A split_signal pair is the highest priority, because the duplicate is distorting trends.
- **Curation (open_conflicts, fragments, attribution_suspects).** Hand off to **artmind-curate**. For attribution suspects, re-extracting the affected notes may be better: `artmind ingest sync --reextract --match <glob>`.
- **A finding worth asking again (any corpus finding).** Promote it to a view with **artmind-create-view**:
  - start from the finding's detector and stats as the question;
  - set `provenance.origin: promoted`;
  - use the finding's title as `source_question`.
- **Privacy.** Snapshots and exports hold vault content. Never copy them into the code checkout.
````

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_skills.py test/test_query_skill_views_step.py test/test_create_view_skill.py -q`. Expected: all PASS.

The views-step test must still pass. Its protocol headings are unchanged, and the new section has no numbered "step N" references.

- [ ] **Step 5: Commit**

```bash
git add artmind/skills/artmind-insights/SKILL.md test/test_insights_skills.py
git commit -m "feat(insights): Insights section in artmind-query; artmind-insights operator skill" -- artmind/skills/artmind-query/SKILL.md artmind/skills/artmind-insights/SKILL.md test/test_insights_skills.py
```

---

### Task 3: Admin-console routes

**Files:**
- Create: `artmind/webui/insights_routes.py`
- Modify: `artmind/webui/app.py` (register it next to `register_dashboard_routes`, in the same admin-only branch)
- Test: `test/test_insights_routes.py` (new)

- [ ] **Step 1: Write the failing test**

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import paths
from artmind.insights import store
from artmind.insights.model import Finding, Run
from artmind.webui import insights_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", tmp_path / "views")
    app = FastAPI()
    insights_routes.register_insights_routes(app)
    return TestClient(app)


def write_run(created="2026-10-17T09:00:00Z"):
    cands = [Finding(key="same_as_candidates:PERSON:a:b", detector="same_as_candidates", kind="health", score=0.9,
                     title="t", action="curate", stats={"canonical": {"_id": "a", "name": "Dad", "class": "PERSON"},
                                                        "alias": {"_id": "b", "name": "Daddy", "class": "PERSON"},
                                                        "rule": "kinship"})]
    run = Run(domain="demo", created_at=created, as_of="2026-10-01", fingerprint={}, anchors=[], profile={"big": 1},
              plan=[], circles=[{"id": "PERSON:a", "name": "Family", "name_source": "llm", "members": ["a", "b"]}],
              diff={"new": 1}, findings=cands + [Finding(key="g", detector="dormancy", kind="corpus", score=0.1,
                                                         title="gone", diff="gone")])
    store.write_run(run)


def test_insights_snapshot_is_trimmed(client):
    write_run()
    r = client.get("/api/insights", params={"domain": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-10-01" and "profile" not in body
    assert [f["key"] for f in body["findings"]] == ["same_as_candidates:PERSON:a:b"]   # gone excluded
    assert body["circles"][0]["name"] == "Family" and body["diff"] == {"new": 1}


def test_missing_snapshot_and_bad_domain(client):
    assert client.get("/api/insights", params={"domain": "demo"}).status_code == 404
    assert client.get("/api/insights", params={"domain": "../x"}).status_code == 400


def test_runs(client):
    write_run()
    assert client.get("/api/insights/runs", params={"domain": "demo"}).json()["runs"][0]["created_at"] == "2026-10-17T09:00:00Z"


def test_refresh_runs_in_background_and_reports_progress(client, monkeypatch):
    calls = []
    monkeypatch.setattr(insights_routes, "_refresh", lambda domain, force: calls.append((domain, force)) or {"status": "written"})
    r = client.post("/api/insights/refresh", json={"domain": "demo", "force": True})
    assert r.status_code == 202
    insights_routes._join_refresh("demo")
    assert calls == [("demo", True)]
    assert client.get("/api/insights/refresh/progress", params={"domain": "demo"}).json()["state"] == "done"


def test_send_to_curation_proposes_guarded_merges(client, monkeypatch):
    write_run()
    seen = {}
    monkeypatch.setattr(insights_routes, "_propose", lambda merges, domain: seen.update(merges=merges, domain=domain)
                        or {"stats": {"proposed": 1}})
    r = client.post("/api/insights/same-as/propose", json={"domain": "demo", "keys": ["same_as_candidates:PERSON:a:b"]})
    assert r.status_code == 200 and seen == {"merges": {"Daddy": "Dad"}, "domain": "demo"}
    assert r.json()["stats"] == {"proposed": 1}


def test_views_catalogue_detail_and_run(client, tmp_path, monkeypatch):
    v = tmp_path / "views" / "people"
    v.mkdir(parents=True)
    (v / "view.yaml").write_text(
        "name: people\nversion: 1\ndomains: [demo]\nsummary: People\nexamples: [\"who\"]\nparams: {}\n"
        "cypher: |\n  MATCH (e:Entity) WHERE e._domain IN $domains RETURN e.name AS name\n"
        "presentation:\n  format: list\n  list: {label: name}\n"
        "provenance: {created: 2026-10-18, origin: authored, source_question: null}\n")
    (v / "SKILL.md").write_text("---\nname: people\ndescription: d\n---\n# people\n")
    cat = client.get("/api/views", params={"domain": "demo"}).json()
    assert [x["name"] for x in cat["views"]] == ["people"]
    assert client.get("/api/views/people").json()["skill_md"].startswith("---")
    monkeypatch.setattr(insights_routes, "_run_view", lambda spec, domains, params: {"status": "ok", "rendered": "- A"})
    out = client.post("/api/views/people/run", json={"domain": "demo", "params": {}}).json()
    assert out == {"status": "ok", "rendered": "- A"}
    assert client.get("/api/views/nope").status_code == 404
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_routes.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `artmind/webui/insights_routes.py`

```python
"""Admin-console routes for the Insights and Views tabs (spec 2026-10-17 §10.4–§10.5).

Read routes serve the latest snapshot or the view catalogue from disk. Refresh
runs `artmind.insights.runner.refresh` in a background thread with a progress
readout. "Send to curation" proposes same-as groups through refine-graph's
review queue; it never merges. Views are read-only here: authoring stays with
the artmind-create-view skill.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_progress: dict[str, dict] = {}
_threads: dict[str, threading.Thread] = {}


class RefreshRequest(BaseModel):
    domain: str
    force: bool = False


class ProposeRequest(BaseModel):
    domain: str
    keys: list[str]


class ViewRunRequest(BaseModel):
    domain: str
    params: dict[str, str] = {}


def _latest(domain: str):
    from artmind.insights import store
    from artmind.insights.model import InsightsError

    try:
        run = store.load_latest(domain)
    except InsightsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail=f"no insights snapshot for {domain}: run a refresh")
    return run


def _refresh(domain: str, force: bool) -> dict:
    from artmind.insights import runner

    return runner.refresh(domain, force=force)


def _propose(merges: dict[str, str], domain: str) -> dict:
    from artmind.refine_graph import propose_merges
    from utils.functions import load_env, resolve_llm_model

    return propose_merges(merges, domain, resolve_llm_model(load_env()))


def _run_view(spec, domains: list[str], params: dict[str, str]) -> dict:
    from artmind.kg_views.runner import run_view

    return run_view(spec, domains, params, render=True)


def _join_refresh(domain: str, timeout: float = 30.0) -> None:
    """Test helper: wait for a background refresh to finish."""
    t = _threads.get(domain)
    if t is not None:
        t.join(timeout)


def register_insights_routes(app: FastAPI) -> FastAPI:
    @app.get("/api/insights")
    async def api_insights(domain: str):
        run = _latest(domain)
        return {"domain": run.domain, "created_at": run.created_at, "as_of": run.as_of, "anchors": run.anchors,
                "diff": run.diff, "llm": run.llm, "circles": run.circles,
                "plan": [p.to_dict() for p in run.plan if p.status != "ran"],
                "findings": [f.to_dict() for f in run.findings if f.diff != "gone"]}

    @app.get("/api/insights/runs")
    async def api_insights_runs(domain: str):
        from artmind.insights import store
        from artmind.insights.model import InsightsError

        try:
            rows = []
            for path in store.list_runs(domain):
                run = store.load_run(path)
                rows.append({"run": path.name, "created_at": run.created_at, "as_of": run.as_of, "diff": run.diff,
                             "findings": sum(1 for f in run.findings if f.diff != "gone")})
        except InsightsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"domain": domain, "runs": list(reversed(rows))}

    @app.post("/api/insights/refresh", status_code=202)
    async def api_insights_refresh(payload: RefreshRequest):
        current = _progress.get(payload.domain, {})
        if current.get("state") == "running":
            raise HTTPException(status_code=409, detail="a refresh is already running for this domain")
        _progress[payload.domain] = {"state": "running", "started": datetime.now(timezone.utc).isoformat()}

        def work():
            try:
                result = _refresh(payload.domain, payload.force)
                _progress[payload.domain] = {"state": "done", "result": result}
            except Exception as exc:  # surfaced to the operator via the progress readout
                _progress[payload.domain] = {"state": "error", "detail": str(exc)}

        t = threading.Thread(target=work, name=f"insights-refresh-{payload.domain}", daemon=True)
        _threads[payload.domain] = t
        t.start()
        return {"state": "running"}

    @app.get("/api/insights/refresh/progress")
    async def api_insights_refresh_progress(domain: str):
        return _progress.get(domain, {"state": "idle"})

    @app.post("/api/insights/same-as/propose")
    async def api_insights_propose(payload: ProposeRequest):
        from artmind.insights.sameas_merges import build_merges

        run = _latest(payload.domain)
        chosen = [f for f in run.findings if f.key in set(payload.keys)]
        merges, candidates, skipped = build_merges(chosen)
        if not merges:
            return {"stats": {"proposed": 0}, "skipped": skipped, "candidates": []}
        result = _propose(merges, payload.domain)
        return {**result, "skipped": skipped, "candidates": candidates}

    @app.get("/api/views")
    async def api_views(domain: str | None = None):
        from artmind.kg_views import store as views_store

        domains = [d.strip() for d in domain.split(",") if d.strip()] if domain else None
        loaded, invalid = views_store.list_views(domains)
        return {"views": [{"name": v.spec.name, "summary": v.spec.summary, "domains": list(v.spec.domains),
                           "examples": list(v.spec.examples), "params": v.spec.to_dict().get("params", {})}
                          for v in loaded], "invalid": invalid}

    @app.get("/api/views/{name}")
    async def api_view(name: str):
        from artmind.kg_views import store as views_store
        from artmind.kg_views.model import ViewError

        try:
            loaded = views_store.load_view(name)
        except ViewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"view": loaded.spec.to_dict(), "skill_md": loaded.skill_md}

    @app.post("/api/views/{name}/run")
    async def api_view_run(name: str, payload: ViewRunRequest):
        import asyncio

        from artmind.kg_views import store as views_store
        from artmind.kg_views.model import ViewError

        try:
            loaded = views_store.load_view(name)
            return await asyncio.to_thread(_run_view, loaded.spec, [payload.domain], payload.params)
        except ViewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
```

Before relying on these calls, read `artmind/kg_views/store.py` (`list_views`, `load_view`) and `model.py` (`ViewSpec.to_dict`, `.examples`, `.domains`) to confirm the attribute names.
- If `list_views` returns `(loaded, invalid)` items with a different shape, adapt the route. The CLI's `_view_summary` in cli.py shows the current fields; reuse it if that is cleaner, without importing cli at module import.
- `load_view` raising `ViewError` for an unknown name must become a 404 on the GET.

In `app.py`, next to `register_dashboard_routes(app, templates)`, add:
- `from artmind.webui.insights_routes import register_insights_routes` with the other imports;
- `register_insights_routes(app)` in the same branch.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_routes.py test/test_webui*.py -q`. Run whichever webui test files exist; `ls test | grep -i webui` lists them. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/insights_routes.py test/test_insights_routes.py
git commit -m "feat(webui): insights and views API routes for the admin console" -- artmind/webui/insights_routes.py artmind/webui/app.py test/test_insights_routes.py
```

---

### Task 4: Insights and Views tabs (frontend)

**Files:**
- Modify: `artmind/webui/templates/dashboard.html`:
  - two tab buttons: `insights` after "Ingest & browse" and `views` after "Schemas";
  - two panels;
  - `<script>` tags for the new JS files, after `dashboard.js`.
- Create: `artmind/webui/static/insights.js`, `artmind/webui/static/views.js`
- Modify: `artmind/webui/static/dashboard.css` (card and network styles, using the existing CSS variables)
- Modify: `artmind/webui/static/app.js` (the `?ask=` prefill)
- Test: `test/test_insights_tabs.py` (new). The markup and asset wiring are checked with TestClient against the real `create_app` in admin mode; see how existing dashboard tests build the app (`grep -rn "dashboard" test/*.py | head`).

**Specification:** the browser check in Task 5 is the acceptance test.

1. **Panels.** The panels follow the existing markup: `.tab-panel[data-tab-group="view"][data-tab-panel="insights"]` containing `section.dash-panel.dash-zone-wide`, then an `h2`, a `p.dash-hint` and a `form.dash-form`.

2. **Insights panel controls:**
   - a domain `<select id="insights-domain">`, filled from `GET /api/domains`;
   - a Refresh button and a "force" checkbox;
   - a status line (`as_of`, `created_at`, diff counts, `llm` stats);
   - the anchors, each with a note "wrong? set `anchors:` in domains/insights/<domain>.yaml";
   - then the sections in spec order:
     1. What changed (`diff` new or changed);
     2. People & circles (communities network card, circle share series, brokers, dormancy);
     3. Trends (salience_trend, type_trend);
     4. Associations (outcome_lift, with the caption shown);
     5. Transitions (timeline);
     6. Open loops;
     7. Graph health, grouped by `action`.

   Load lazily the first time the tab is clicked, the same pattern as `cli-guide`, and remember the selected domain in `localStorage` inside try/catch.

3. **Cards** (`insights.js`):
   - **Contents:** the title, the `narration` under it with a small "generated" label, a score bar, and a per-card body by `card.type`:
     - `series`: an inline SVG sparkline with the period labels and values;
     - `ranking` / `stat`: key stats as a definition list;
     - `pairs`: share vs baseline, plus `card.caption`;
     - `timeline`: an ordered list of `events` (date, kind, label);
     - `network`: described below.
   - **Rendering rule:** build all text with `el()` / `textContent`. Never set `innerHTML` from snapshot data.
   - **Ask in console:** opens `/?ask=` + `encodeURIComponent(question)` in a new tab. The question is: `"About the insight '<title>' in <domain>: explain it and check it against the graph (subjects: <name> (<_id>), …)."`
   - **Copy promote-to-view prompt:** copies `"Use artmind-create-view to promote this insight into a view for domain <domain>. Finding <key> (<detector>): <title>. Stats: <JSON of stats, first 1500 chars>. Set provenance.origin: promoted and source_question: \"<title>\"."` to the clipboard via `navigator.clipboard.writeText`, then shows "copied".
   - **Send to curation:** only on `same_as_candidates` and `split_signal` cards. It confirms with the operator first, then sends `POST /api/insights/same-as/propose {domain, keys:[key]}` and shows the result's `stats` and `skipped`. For split_signal cards, send the keys of the matching same_as_candidates findings, the ones whose two `_id`s equal its two subjects.

4. **Network card** (communities `circle` findings): a self-contained force layout in `insights.js`, about 100 lines, with no library.
   - **Layout:** nodes are positioned by repulsion between all pairs, spring attraction on edges weighted by `weight`, and centring, for 300 iterations with cooling.
   - **Drawing:** the result is drawn into an `<svg viewBox>`. Node radius scales with weighted degree, nodes are coloured by circle (one colour per circle finding; with one card per circle, colour by whether the node is a broker), and broker nodes, whose `_id` appears in any `brokers` finding, get an outline. Labels are drawn for the top 12 nodes by weight; every node has a `<title>`.
   - **Caps:** the card shows `card.truncated` as "showing 60 of N". Work in an O(n²) loop only up to 60 nodes.

5. **Refresh:** `POST /api/insights/refresh`, then poll `/api/insights/refresh/progress` every 1.5 s until the state is `done` or `error`. On `done`, reload. On `error`, show its detail. While running, disable the button.

6. **Views panel** (`views.js`):
   - **Layout:** a domain select and the catalogue list (name, summary, examples).
   - **Selecting a view:** shows its summary and a form generated from `params`:
     - `int`/`float` become number inputs;
     - `bool` becomes a checkbox;
     - `enum` becomes a select;
     - `date` becomes a date input;
     - `entity` becomes a text input with the hint "name, or @<_id>";
     - `list` becomes a text input (comma-separated);
     - each input is pre-filled with its `default`.
   - **Run:** `POST /api/views/{name}/run`.
     - `status: ok` renders `rendered` with the vendored `marked` and then `DOMPurify.sanitize` into the result area. Check `templates/index.html` for the vendored script tags and how app.js calls them.
     - `needs_disambiguation` / `no_match` lists the candidates or suggestions as buttons that re-run with `@<_id>`.
   - **Collapsible guidance:** a `<details>` shows the view's `SKILL.md` as plain text.
   - **Read-only:** no editing.

7. **`app.js` prefill.** On load, if `new URLSearchParams(location.search).get("ask")` is present, put it in the chat input, focus it and do not send. Remove `ask` from the URL with `history.replaceState`, keeping `?c=` if present. Read app.js's input element id first.

8. **CSS:** use the existing variables and classes. Add `.ins-card`, `.ins-grid` (a responsive grid), `.ins-narration` (muted), `.ins-generated`, `.ins-net svg`, `.ins-actions`, and dark-mode-safe colours via the existing tokens. Read the top of dashboard.css for its variables.

**Test** (`test/test_insights_tabs.py`):
- `/dashboard` HTML contains `data-tab="insights"`, `data-tab="views"`, `insights.js` and `views.js`;
- `GET` on both static files returns 200;
- `insights.js` contains no `innerHTML =` assignment, except clearing with `innerHTML = ""`: grep in the test.

- [ ] Write the test, see it fail, implement, see it pass.
- [ ] Commit only these paths with the message "feat(webui): Insights and Views tabs in the admin console".

---

### Task 5: Browser verification and docs (controller)

- **Launch config.** Add `.claude/launch.json` with an `admin-ui` entry. It runs `artmind admin-ui --port 8765` with cwd `~/artmind_vaults/brain01`, using the env or `runtimeArgs` the preview tool supports. If cwd is not supported, use `ARTMIND_VAULT`.
- **Insights tab checks** (open it on `personal_journal`):
  - the cards render;
  - captions are marked generated;
  - the network SVG draws;
  - Copy prompt works;
  - Ask in console opens the console with the prefilled question;
  - the console shows no errors.
- **Views tab checks:** the catalogue lists brain01's two views, and one runs (the trips view, for a person and place).
- **Proof:** take screenshots as proof. Never paste vault content into the repo.
- **Docs:**
  - CLAUDE.md: the `query insights` route and the routing line; the Insights and Views tabs in the webui row; the `artmind-insights` skill.
  - Spec: the §10 phase-4 "as built" notes, covering the decisions above.
