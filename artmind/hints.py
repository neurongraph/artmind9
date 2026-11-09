"""Signposts (orient spec §1.1): one runnable command when a result is empty or thin;
facts about the result, never about the question.

Each hint takes the payload (or row) a command just built and returns a `next` string,
or `None` when the result is not empty/thin. Pure: no I/O, no graph, no LLM.
"""
from __future__ import annotations

import shlex


def join_next(first: str | None, second: str | None) -> str | None:
    """Join two `next` strings with "; " (as `execute_pattern` does); either may be None."""
    if first and second:
        return f"{first}; {second}"
    return first or second or None


def domain_flags(domains) -> str:
    """`--domain x --domain y`."""
    return " ".join(f"--domain {d}" for d in domains)


def quote(text) -> str:
    """POSIX-shell-safe (`shlex.quote`): single quotes, `'` as `'"'"'`."""
    return shlex.quote(str(text if text is not None else ""))


def value_or(text, placeholder: str) -> str:
    """A quoted real value, or the `"<placeholder>"` the agent is to fill in."""
    return quote(text) if text else f'"{placeholder}"'


def pattern6_empty(payload: dict, domains, name1, name2, class1=None, class2=None) -> str | None:
    if payload.get("rows") != []:
        return None
    return (
        f"artmind query graph pattern5 {domain_flags(domains)} "
        f"--entityClass1 {class1 or '<CLASS1>'} --entityClass2 {class2 or '<CLASS2>'} "
        f"--entityName1 {value_or(name1, '<name1>')} --entityName2 {value_or(name2, '<name2>')} "
        f"--mode shortest --compact"
    )


def pattern1_empty(payload: dict, domains) -> str | None:
    if payload.get("rows") != []:
        return None
    return f'artmind query graph entity-listing {domain_flags(domains)} --nameFilter "<word>" --compact'


def hierarchy_empty(payload: dict, domains, question) -> str | None:
    summary = payload.get("summary")
    if not isinstance(summary, dict) or summary.get("total") != 0:
        return None
    entity_class = (payload.get("root") or {}).get("entity_class") or "<CLASS>"
    return (
        f"artmind query graph pattern1 {domain_flags(domains)} --entityClass {entity_class} "
        f"--compact {value_or(question, '<question>')}"
    )


def resolve_key_empty(payload: dict, domains, phrase, top_k: int) -> str | None:
    if payload.get("candidates") != []:
        return None
    if top_k < 25:
        return f"artmind query resolve-key {domain_flags(domains)} --topK 25 --compact {quote(phrase)}"
    return f"artmind query entity-resolve {domain_flags(domains)} --compact {quote(phrase)}"


def text2cypher_empty(payload: dict, domains, question) -> str | None:
    if payload.get("dry_run") or payload.get("rows_total") != 0:
        return None
    d = domain_flags(domains)
    return (
        f"artmind query graph text2cypher {d} --dryRun --compact {quote(question)}; "
        f"artmind query orient {d} --compact"
    )


def text2sql_empty(payload: dict, domains, question) -> str | None:
    if payload.get("dry_run") or payload.get("rows") != []:
        return None
    d = domain_flags(domains)
    return f"artmind query text2sql {d} --dryRun --compact {quote(question)}; artmind db schema {d} --compact"


def text2_failure(kind: str, domains, question, dry_run: bool) -> str:
    """Text appended to a text2cypher / text2sql error message."""
    d = domain_flags(domains)
    if dry_run:
        return f"\nnext: artmind query orient {d} --compact"
    cmd = "graph text2cypher" if kind == "text2cypher" else "text2sql"
    return f"\nnext: artmind query {cmd} {d} --dryRun --compact {quote(question)}"


def entity_context_thin(row: dict, domains) -> str | None:
    """A row with fewer than two chunks but source documents: open the chunk or the document."""
    if (row.get("chunks_total") or 0) >= 2 or not (row.get("source_documents_total") or 0) > 0:
        return None
    ids = [c.get("chunk_id") or c.get("id") for c in (row.get("chunks") or [])]
    ids = [i for i in ids if i]
    if ids:
        return f"artmind query chunks --shape brief --idList {','.join(ids)} --expand 1 --compact"
    docs = row.get("source_documents") or []
    name = (docs[0].get("name") or docs[0].get("title")) if docs and isinstance(docs[0], dict) else None
    return f"artmind query graph pattern10 {domain_flags(domains)} --documentName {value_or(name, '<document>')} --compact"


def entity_context_cut(row: dict, domains, max_connections: int) -> str | None:
    """Connections were cut by --maxConnections: list them all with pattern8."""
    total = row.get("connections_total") or 0
    if total <= max_connections:
        return None
    entity = row.get("entityData") or row.get("entity") or {}
    eid = entity.get("_id") or entity.get("id")
    if not eid:
        return None
    return (
        f"artmind query graph pattern8 {domain_flags(domains)} --entityId {eid} "
        f"--entityClass <CLASS> --all --compact"
    )


def predecessor_next(payload: dict) -> str | None:
    """document-history for the highest-ranked returned document that replaced older versions.

    Reads vector-text rows (`doc.replaced` brief, `document.supersedes` full) and survey
    rows (`replaced` brief, `supersedes` full); `None` when no document replaced another.
    """
    for row in (payload.get("rows") or []) + (payload.get("documents") or []):
        if not isinstance(row, dict):
            continue
        doc = row.get("doc") if isinstance(row.get("doc"), dict) else row.get("document")
        doc = doc if isinstance(doc, dict) else row
        if not (doc.get("replaced") or doc.get("supersedes")):
            continue
        domain = doc.get("domain") or doc.get("_domain")
        ident = doc.get("name") or doc.get("id") or doc.get("doc_id")
        if domain and ident:
            return f"artmind query document-history --domain {quote(domain)} --documentName {quote(ident)} --compact"
    return None
