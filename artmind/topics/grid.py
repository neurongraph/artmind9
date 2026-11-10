"""The faceted grid of a topic (spec 2026-11-10 §6–§7.2): subject × domain × period cells,
each cell's keyword `matching` count, its `take`, a fair cut under `max_docs`, and the
per-cell gather. `topic build` and `query topics run` both gather through `gather`, so the
agent reads what the build read.

One keyword count query per subject (`_MATCHING`) gives every cell of that subject: the
distinct documents with a passage hit in scope, with their date and domain. Each cell is
then one `survey_search` with the subject as the question, the cell's domain and date
window, and `top_docs = take`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from artmind.topics.gather import MAX_PASSAGES_PER_DOC, Material, Source

HIT_LIMIT = 5000

#: Distinct documents with a keyword hit for one subject, with their date and domain.
#: `scanned` is the passages the index returned before the domain filter: at HIT_LIMIT
#: the counts are a floor.
_MATCHING = """
CALL db.index.fulltext.queryNodes('passage_text_ft', $ft_query, {limit: $hit_limit})
YIELD node AS p
WITH count(p) AS scanned, collect(DISTINCT CASE WHEN p._domain IN $domains THEN p.doc_id END) AS ids
OPTIONAL MATCH (d:Document) WHERE d.id IN ids
RETURN scanned, collect(CASE WHEN d IS NULL THEN NULL ELSE
       {doc_id: d.id, date: coalesce(d._valid_from, d.valid_from), domain: d._domain} END) AS docs
"""


@dataclass
class Cell:
    subject: int                 # 1-based index into the plan's subjects
    period: str | None = None    # "2023", "2023-04", "undated", or None (no period slice)
    domain: str | None = None    # a child domain when split by domain
    matching: int = 0
    take: int = 0
    at_least: bool = False
    doc_ids: list[str] = field(default_factory=list)   # gathered, in rank order

    @property
    def partial(self) -> bool:
        return self.matching > len(self.doc_ids) if self.doc_ids else self.matching > self.take

    def label(self) -> str:
        parts = [f"subject {self.subject}"]
        if self.domain:
            parts.append(self.domain)
        if self.period:
            parts.append(self.period)
        return " / ".join(parts)

    def to_json(self, gathered: bool = True) -> dict:
        out = {"label": self.label(), "subject": self.subject, "period": self.period, "domain": self.domain,
               "matching": self.matching, "take": self.take}
        if self.at_least:
            out["at_least"] = True
        if gathered:
            out["taken"] = len(self.doc_ids)
            out["partial"] = self.matching > len(self.doc_ids)
        else:
            out["partial"] = self.matching > self.take
        return out


def period_of(date, by: str) -> str:
    text = str(date or "")
    if len(text) < (4 if by == "year" else 7) or not text[:4].isdigit():
        return "undated"
    return text[:4] if by == "year" else text[:7]


def period_bounds(period: str | None) -> tuple[str | None, str | None]:
    import calendar

    if not period or period == "undated":
        return None, None
    if len(period) == 4:
        return f"{period}-01-01", f"{period}-12-31"
    year, month = int(period[:4]), int(period[5:7])
    return f"{period}-01", f"{period}-{calendar.monthrange(year, month)[1]:02d}"


def cell_domain(domain: str | None, roots: list[str]) -> str | None:
    """The child of a plan domain that holds `domain` (the root itself for its own documents)."""
    if not domain:
        return None
    for root in sorted(roots, key=len, reverse=True):
        if domain == root:
            return root
        if domain.startswith(root + "."):
            return root + "." + domain[len(root) + 1:].split(".", 1)[0]
    return domain


def _period_sort(p) -> tuple:
    return (p is None, p == "undated", p or "")


def count_subject(session, subject: str, domains: list[str]) -> tuple[list[dict], bool]:
    """(matching documents [{doc_id, date, domain}], at_least) for one subject."""
    from artmind.graph_query import sanitize_lucene_query, serialize_record

    ft_query = sanitize_lucene_query(subject, drop_stopwords=True)
    if not ft_query:
        return [], False
    rec = session.run(_MATCHING, ft_query=ft_query, domains=domains, hit_limit=HIT_LIMIT).single()
    if rec is None:
        return [], False
    row = serialize_record(rec)
    docs = [d for d in row.get("docs") or [] if isinstance(d, dict) and d.get("doc_id")]
    return docs, int(row.get("scanned") or 0) >= HIT_LIMIT


def count_all(subjects: list[str], domains: list[str], *, session=None, expand=None
              ) -> tuple[list[list[dict]], list[bool]]:
    """Per subject: (matching documents, at_least). One keyword query each."""
    from artmind.graph_query import read_session

    if expand is None:
        from artmind.vector_query import _expand as expand
    scoped = expand(list(domains))
    counted, floors = [], []
    own = session is None
    ctx = read_session() if own else None
    session = ctx.__enter__() if own else session
    try:
        for subject in subjects:
            docs, at_least = count_subject(session, subject, scoped)
            counted.append(docs)
            floors.append(at_least)
    finally:
        if own:
            ctx.__exit__(None, None, None)
    return counted, floors


def cells_from(counted: list[list[dict]], floors: list[bool], domains: list[str], by: str, split: str,
               per_cell: int, max_docs: int) -> tuple[list[Cell], dict]:
    def key_of(doc) -> tuple:
        period = period_of(doc.get("date"), by) if by != "none" else None
        dom = cell_domain(doc.get("domain"), list(domains)) if split == "domain" else None
        return dom, period

    keys = sorted({key_of(d) for docs in counted for d in docs},
                  key=lambda k: ((k[0] or ""), _period_sort(k[1])))
    if not keys:
        keys = [(None, None)]
    cells: list[Cell] = []
    for i, docs in enumerate(counted, 1):
        per_key: dict = {}
        for d in docs:
            per_key[key_of(d)] = per_key.get(key_of(d), 0) + 1
        for dom, period in keys:
            n = per_key.get((dom, period), 0)
            # An undated cell cannot be windowed by date: counted, never gathered.
            want = 0 if period == "undated" else min(n, per_cell)
            cells.append(Cell(i, period, dom, n, want, floors[i - 1]))
    return cells, fair_cut(cells, max_docs)


def plan_cells(subjects: list[str], domains: list[str], by: str, split: str, per_cell: int, max_docs: int,
               *, session=None, expand=None) -> tuple[list[Cell], dict]:
    """Every cell of the grid with `matching` and a fair `take`; also a report:
    `{cut, slots, wanted, max_docs}`."""
    counted, floors = count_all(subjects, domains, session=session, expand=expand)
    return cells_from(counted, floors, domains, by, split, per_cell, max_docs)


def fair_cut(cells: list[Cell], max_docs: int) -> dict:
    """Cut takes over `max_docs` round-robin (one slot per cell per round, in cell order)."""
    wanted = sum(c.take for c in cells)
    if wanted <= max_docs:
        return {"cut": False, "slots": wanted, "wanted": wanted, "max_docs": max_docs}
    given = {id(c): 0 for c in cells}
    left, rnd = max_docs, 0
    while left > 0:
        rnd += 1
        progressed = False
        for c in cells:
            if left == 0:
                break
            if c.take >= rnd:
                given[id(c)] += 1
                left -= 1
                progressed = True
        if not progressed:
            break
    for c in cells:
        c.take = given[id(c)]
    return {"cut": True, "slots": max_docs, "wanted": wanted, "max_docs": max_docs}


def gather(subjects: list[str], cells: list[Cell], domains: list[str], *, passages: int = MAX_PASSAGES_PER_DOC,
           search=None, concurrency: int = 4) -> tuple[list[Material], dict[str, dict]]:
    """Run one survey per cell with `take > 0`; merge documents by id.

    Returns (materials in first-seen order, survey rows by (cell label, doc id)) — the
    rows keep each cell's own rank and passages for `query topics run`."""
    if search is None:
        from artmind.survey import survey_search as search
    from concurrent.futures import ThreadPoolExecutor

    def one(cell: Cell) -> dict:
        date_from, date_to = period_bounds(cell.period)
        kwargs = {"top_docs": cell.take, "passages": passages}
        if date_from:
            kwargs.update(date_from=date_from, date_to=date_to)
        scope = [cell.domain] if cell.domain else list(domains)
        return search(scope, subjects[cell.subject - 1], **kwargs)

    todo = [c for c in cells if c.take > 0]
    if concurrency > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(todo))) as pool:
            payloads = list(pool.map(one, todo))
    else:
        payloads = [one(c) for c in todo]
    materials: dict[str, Material] = {}
    per_cell: dict[tuple[str, str], dict] = {}
    for cell, payload in zip(todo, payloads):
        for doc in (payload.get("documents") or [])[: cell.take]:
            doc_id = doc.get("doc_id")
            if not doc_id:
                continue
            cell.doc_ids.append(doc_id)
            per_cell[(cell.label(), doc_id)] = doc
            m = materials.get(doc_id)
            if m is None:
                m = materials[doc_id] = Material(doc_id, doc.get("title"), doc.get("name"), doc.get("domain"),
                                                 doc.get("summary"), date=doc.get("date"), status=doc.get("status"))
            m.cells.append(cell.label())
            for p in doc.get("passages") or []:
                m.add(Source(p["id"], p["chunk_id"], p.get("text") or ""))
    return list(materials.values()), per_cell
