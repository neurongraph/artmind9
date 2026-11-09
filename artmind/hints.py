"""Signposts (orient spec §1.1): one runnable command when a result is empty or thin;
facts about the result, never about the question.

Each hint takes the payload (or row) a command just built and returns a `next` string,
or `None` when the result is not empty/thin. Pure: no I/O, no graph, no LLM.
"""
from __future__ import annotations


def join_next(first: str | None, second: str | None) -> str | None:
    """Join two `next` strings with "; " (as `execute_pattern` does); either may be None."""
    if first and second:
        return f"{first}; {second}"
    return first or second or None


def domain_flags(domains) -> str:
    """`--domain x --domain y`."""
    return " ".join(f"--domain {d}" for d in domains)


def quote(text) -> str:
    """Double-quoted, inner double quotes escaped."""
    return '"' + str(text if text is not None else "").replace('"', '\\"') + '"'


def pattern6_empty(payload: dict, domains, name1, name2, class1=None, class2=None) -> str | None:
    if payload.get("rows") != []:
        return None
    return (
        f"artmind query graph pattern5 {domain_flags(domains)} "
        f"--entityClass1 {class1 or '<CLASS1>'} --entityClass2 {class2 or '<CLASS2>'} "
        f"--entityName1 {quote(name1 or '<name1>')} --entityName2 {quote(name2 or '<name2>')} "
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
        f"--compact {quote(question or '<question>')}"
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
