"""A synthetic corpus for faceted topic builds: a fake graph session (keyword counts and
footnote links), a fake survey and recording LLMs. Synthetic text only."""
from _neo4j_recorder import Recorder

from artmind.graph_query import sanitize_lucene_query

#: doc id -> (date, domain, text)
DOCS = {
    "D1": ("2023-03-01", "j", "Planted tomato seedlings in the raised bed."),
    "D2": ("2023-07-12", "j", "Turned the compost heap; tomato harvest began."),
    "D3": ("2024-05-02", "j", "New compost bin built from pallets."),
}
#: subject -> doc ids whose words it matches
MATCHES = {"tomato planting": ["D1", "D2"], "compost heap": ["D2", "D3"]}
CARDS = [{"domain": "j", "unit": {"documents": 100}, "time": {"dated": {"from_content": 100},
                                                               "span": ["2021-01-01", "2024-12-31"]}}]


def ft(text):
    return sanitize_lucene_query(text, drop_stopwords=True)


def session_factory(versions=None):
    versions = versions or {}
    by_ft = {ft(s): ids for s, ids in MATCHES.items()}

    def respond(cypher, params):
        if "passage_text_ft" in cypher and "hit_limit" in params:
            ids = by_ft.get(params["ft_query"], [])
            return [{"scanned": len(ids), "docs": [{"doc_id": d, "date": DOCS[d][0], "domain": DOCS[d][1]}
                                                   for d in ids]}]
        if "UNWIND $chunkIds" in cypher:
            return [{"chunk_id": c, "header_path": None, "doc_id": c[:2], "name": f"{c[:2]}.md", "title": f"T{c[:2]}",
                     "path": f"notes/{c[:2]}.md", "source_path": None, "version": versions.get(c[:2], f"v-{c[:2]}")}
                    for c in params["chunkIds"]]
        return []

    session = Recorder(respond)
    return session, (lambda: session)


def search(domains, question, **kw):
    lo, hi = kw.get("date_from") or "0000", kw.get("date_to") or "9999"
    ids = [d for d in MATCHES.get(question, []) if lo <= DOCS[d][0] <= hi
           and any(DOCS[d][1] == x or DOCS[d][1].startswith(x + ".") for x in domains)][: kw["top_docs"]]
    return {"documents": [{"doc_id": d, "title": f"T{d}", "name": f"{d}.md", "domain": DOCS[d][1], "date": DOCS[d][0],
                           "status": "current", "summary": None,
                           "passages": [{"id": f"{d}c_p01", "chunk_id": f"{d}c", "text": DOCS[d][2]}]}
                          for d in ids]}


class LLMs:
    def __init__(self):
        self.maps, self.reduces = [], []

    def map(self, prompt):
        self.maps.append(prompt)
        pid = prompt.split("[", 2)[1].split("]", 1)[0] if "\n[" in prompt else None
        for line in prompt.splitlines():
            if line.startswith("[") and "]" in line:
                pid, text = line[1:].split("] ", 1)
                return {"relevant": True, "notes": [{"claim": text, "passages": [pid]}]}
        return {"relevant": False, "notes": []}

    def reduce(self, prompt):
        self.reduces.append(prompt)
        ids = [line.split("[#")[1].split("]")[0] for line in prompt.splitlines() if "[#" in line]
        if prompt.startswith("Topic:"):  # the overview
            return f"## Overview\n\nGarden work over three seasons [#{ids[0]}].\n\n## Where sources differ\n\nNo disagreements found."
        return " ".join(f"Something happened [#{i}]." for i in ids)
