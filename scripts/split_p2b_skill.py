"""One-off: build artmind/skills/artmind-query-p2b from the live artmind-query text (P2b, spec 2026-11-11).

Every moved line is copied verbatim; test/test_query_skill_p2b.py checks each lands in exactly one file.
Run from the checkout root: uv run python scripts/split_p2b_skill.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "artmind" / "skills" / "artmind-query"
DST = ROOT / "artmind" / "skills" / "artmind-query-p2b"

lines = (SRC / "SKILL.md").read_text(encoding="utf-8").splitlines()


def at(prefix: str) -> int:
    hits = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


def span(start: str, end: str | None) -> list[str]:
    return lines[at(start):(at(end) if end else len(lines))]


def body(chunk: list[str]) -> str:
    return "\n".join(chunk).strip("\n") + "\n"


HEAD = lines[:at("## Fixed Structural Schema")]
TAIL = span("## Fallback Ladder", None)

refs: dict[str, list[str]] = {
    "graph-model": ["# Graph model and the pattern commands",
                    "Read when a result carries `guide: graph-model` (any `pattern*`, `text2cypher`), when you write "
                    "Cypher, or when orient was cut.", "",
                    *span("## Fixed Structural Schema", "## Insights"), "",
                    *span("### 2. Discover", "### 3. Resolve"), "",
                    *span("### 4. Retrieve", "**Superseded history.**")],
    "insights": ["# Insights and the catalogue",
                 "Read for broad or retrospective questions, or to see what a domain already answers.", "",
                 *span("## Insights", "## Survey")],
    "survey": ["# Survey and topic plans",
               "Read when a result carries `guide: survey`, or before a question that spans documents.", "",
               *span("## Survey", "## The Query Protocol")],
    "views": ["# Saved views",
              "Read when orient lists a view whose `summary` or `examples` fit the question.", "",
              *span("### 1. Views", "#### Store routing")],
    "composites": ["# The mart: facts, dimensions and wholes",
                   "Read when a result carries `guide: composites` (a `text2sql` answer from `<d>__mart_*`), or when "
                   "orient's card lists `mart`.", "",
                   *span("A card with `mart`", "### 2. Discover")],
    "resolve": ["# Resolve names to entities",
                "Read when a result carries `guide: resolve` (`twins`, `needs_disambiguation`, `no_match`).", "",
                *span("### 3. Resolve", "### 4. Retrieve")],
    "hubs": ["# Hub entities and grounding",
             "Read when a result carries `guide: hubs` (`hub: true` or a `cut`), or before pulling source text.", "",
             *span("#### Hub entities", "### 5. Ground"), "",
             *span("### 5. Ground", "### 6. Adjudicate")],
}
appends = {
    "structured-store": span("#### Store routing", "A card with `mart`"),
    "temporal": span("**Superseded history.**", "#### Hub entities"),
    "conflicts": span("### 6. Adjudicate", "## Fallback Ladder"),
}

READ_EVERY_RESULT_END = "safe to skip when the question does not need what it offers."
GUIDE_LINES = [
    "",
    "**`guide` is an instruction.** A result with `guide: [\"hubs\"]` (and a `guide:` line in `warnings`) means:",
    "open `references/hubs.md` in this skill's folder with the Read tool now, before your next command, then",
    "continue. Once per file per turn; never answer before reading a file a `guide` named this turn.",
]
RUNNING_COMMANDS_END = "A refused command is not a reason to stop: re-run it bare."
COMMANDS_LINES = [
    "",
    "`pattern*`, `text2cypher`, `timeline`, `conflicts`, `hierarchy`, `metadata`, `structural-metadata` and",
    "`entity-listing` sit under `artmind query graph`; every other query command under `artmind query`. Every option",
    "of every command is in `references/commands.md`.",
]

ROUTING = """## Views and routing — question shape → command → reference

Match every question against the saved views `orient --domain` listed first: a view that fits answers in one call.
Otherwise pick a row for each part. Before an `entity-context`, `entity-history` or `pattern*` call, resolve each
name with `artmind query entity-resolve --domain <d> --topK 5 --compact "<name>"` and use the chosen `_id`. Read a
row's reference before its first command this turn. Every command needs `--domain <d>` and `--compact`.

| Question shape | First command | Reference |
|---|---|---|
| A saved view's `summary` or `examples` describe it | `artmind query views show <name>`, then `artmind query views run <name> --param <p>=<value> --render markdown` | `views.md` |
| One entity: tell me about X, X's role, why X … | `artmind query entity-context --shape brief --entityId <id> --query "<question>"` | `hubs.md` |
| Text of chunk ids you already have | `artmind query chunks --shape brief --idList <id>` | `hubs.md` |
| Where, when or how something happened; quotes; no entity anchor | `artmind query vector-text --shape brief --topK 10 "<part>"` | `hubs.md` |
| Spans documents: every time, each year, a trend, across the notes | `artmind query survey --shape brief [--by year] "<part>"`, or `artmind query topics run <slug>` when orient lists a plan for it | `survey.md` |
| Counts, sums or groups over records, or a card lists `mart` | `artmind query text2sql "<question>"` | `structured-store.md`, `composites.md` |
| A code, id or key value held in a table | `artmind query resolve-key` | `structured-store.md` |
| Entities of a class; the main or top ones | `artmind query graph pattern1 --shape brief --entityClass <LABEL>`; `artmind query graph pattern9 --entityClass <LABEL> --topN 5` | `graph-model.md` |
| Which or how many X did Y … | `artmind query graph pattern8 --entityClass X --entityId <id> --via mentions --all` | `graph-model.md` |
| How X and Y are related | `artmind query graph pattern6 --entityId1 <id> --entityId2 <id>`, then `artmind query graph pattern5 --mode shortest` | `graph-model.md` |
| Entities by a name fragment | `artmind query graph pattern7 --shape brief --searchTerm "<fragment>"` | `graph-model.md` |
| All chunks of a document, or its summary | `artmind query graph pattern10 --documentName "<name>"` | `graph-model.md` |
| Aggregations or filters no row above covers | `artmind query graph text2cypher --dryRun "<question>"`, then without `--dryRun` | `graph-model.md` |
| Everyone under X, a reporting line, a chain of command | `artmind query graph hierarchy --entityId <id>` | `hierarchy.md` |
| X's value before it changed, or at a date | `artmind query entity-history --entityId <id> [--property P] [--asOf <date>]` | `temporal.md` |
| What changed when a document was edited; an earlier version | `artmind query document-history --documentName "<name>"` | `temporal.md` |
| A sequence of events across the domain, by date | `artmind query graph timeline` | `temporal.md` |
| Sources that may disagree; comparing claims | `artmind query graph conflicts` | `conflicts.md` |
| Broad or retrospective: what stands out, what changed over time | `artmind query insights list` | `insights.md` |
| What the corpus already answers | `artmind query assets`; `artmind query catalogue` | `insights.md` |
| Orient was cut and a `cuts` entry names a `rest` | the `rest` command | `graph-model.md` |
"""

LADDER_END = "4. vector-text sparse or weak → state that the available artmind data does not answer the question."
JUDGEMENT_LINES = [
    "",
    "Judgement, whatever the command:",
    "",
    "- A count, a list or a ranking needs the whole set: answer it from `complete: true`, or say N of M.",
    "- Never say a thing is absent from a graph result alone: search the text (`vector-text`, `survey`) first.",
    "- Retired documents never appear in search; a dated or 'what changed' question also reads `references/temporal.md`.",
    "- Two sources that disagree are both stated, each with its document; never blend them.",
    "- Say plainly which part the data does not answer, and why (empty, partial, not read).",
]

# Cross-references to sections that moved; the only edits to moved text. (file, old, new, expected count)
REWRITES: tuple[tuple[str, str, str, int], ...] = (
    ("SKILL.md", "(Views below)", "(the routing table below)", 1),
    ("SKILL.md", "from orient rule 4 and the Query Protocol", "from orient rule 4 and the routing table", 1),
    ("SKILL.md", "The ladder starts after the Views check:", "The ladder starts after the views check in the routing table:", 1),
    ("graph-model.md", "see Adjudicate below", "see Adjudicate in `references/conflicts.md`", 1),
    ("graph-model.md", "(see Retrieve)", "(see the command table below)", 1),
    ("graph-model.md", "\"Hub entities\" below", "`references/hubs.md`", 3),
    ("graph-model.md", "see \"Store routing\" below", "see Store routing in `references/structured-store.md`", 1),
    ("graph-model.md", "(see Views)", "(see `references/views.md`)", 1),
    ("graph-model.md", "\u2014 see Views |", "\u2014 see `references/views.md` |", 1),
    ("structured-store.md", "See \"Store routing\" below", "See \"Store routing\" at the end of this file", 1),
    ("structured-store.md", "Discover/Resolve/Retrieve as documented below", "the routing table in SKILL.md", 1),
    ("temporal.md", "the History labels in SKILL.md", "the History labels in `references/graph-model.md`", 1),
)


def apply_rewrites() -> None:
    for name, old, new, count in REWRITES:
        path = DST / name if name == "SKILL.md" else DST / "references" / name
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == count, (name, old, text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")


def insert_after(chunk: list[str], endswith: str, extra: list[str]) -> list[str]:
    idx = [i for i, ln in enumerate(chunk) if ln.rstrip().endswith(endswith)]
    assert len(idx) == 1, (endswith, idx)
    return chunk[: idx[0] + 1] + extra + chunk[idx[0] + 1:]


def main() -> None:
    (DST / "references").mkdir(parents=True, exist_ok=True)
    head = ["name: artmind-query-p2b" if ln == "name: artmind-query" else ln for ln in HEAD]
    head = insert_after(head, READ_EVERY_RESULT_END, GUIDE_LINES)
    head = insert_after(head, RUNNING_COMMANDS_END, COMMANDS_LINES)
    tail = insert_after(TAIL, LADDER_END, JUDGEMENT_LINES)
    skill = "\n".join(head).rstrip("\n") + "\n\n" + ROUTING + "\n" + "\n".join(tail).rstrip("\n") + "\n"
    (DST / "SKILL.md").write_text(skill, encoding="utf-8")
    for name, chunk in refs.items():
        (DST / "references" / f"{name}.md").write_text(body(chunk), encoding="utf-8")
    for name in ("structured-store", "temporal", "conflicts", "hierarchy"):
        text = (SRC / "references" / f"{name}.md").read_text(encoding="utf-8").rstrip("\n") + "\n"
        if name in appends:
            text += "\n" + body(appends[name])
        (DST / "references" / f"{name}.md").write_text(text, encoding="utf-8")
    apply_rewrites()
    print(f"SKILL.md {len(skill.splitlines())} lines; references: "
          + ", ".join(sorted(p.stem for p in (DST / "references").glob("*.md"))))


if __name__ == "__main__":
    main()
