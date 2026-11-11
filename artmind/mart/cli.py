"""`artmind mart …` (spec §4). Attached to the root group in artmind/cli.py."""
from __future__ import annotations

import click

from artmind.cli_conventions import Change, Effect, compact_option, dry_run_option, with_effect
from artmind.mart import enrich as enrich_mod
from artmind.mart import records
from artmind.mart.declaration import MartDeclarationError, load_declaration


def _echo(payload, compact):
    from artmind.cli import _echo_json

    _echo_json(payload, compact)


def _domains(domain):
    from artmind.cli import _parse_domains

    return _parse_domains(domain)


def _declaration(domain):
    try:
        decl = load_declaration(domain)
    except MartDeclarationError as e:
        raise click.ClickException(str(e)) from e
    if decl is None:
        raise click.ClickException(f"no mart declaration for {domain!r}; write domains/mart/{domain}.yaml")
    return decl


@click.group("mart")
def mart_group() -> None:
    """The mart: facts, dimensions and wholes in the structured store (spec 2026-11-10)."""


@mart_group.command("enrich")
@click.option("--domain", "domain", required=True, help="Domain with a mart declaration")
@click.option("--llm/--noLlm", "use_llm", default=False, help="Ask the LLM for members rules cannot place")
@click.option("--limit", type=int, default=None, help="At most N LLM calls")
@click.option("--dimension", "dimensions", multiple=True, type=click.Choice(["person", "place", "type"]),
              help="Only ask the LLM about these dimensions (repeatable)")
@dry_run_option("Count what rules, seeds and the LLM would decide; write nothing, call nothing")
@compact_option
def mart_enrich(domain, use_llm, limit, dimensions, dry_run, compact):
    """Decide dimension members: user records, rules, graph seeds, then (with --llm) the LLM."""
    decl = _declaration(domain)
    llm = None
    if use_llm and not dry_run:
        from artmind.mart import llm as mart_llm

        llm = mart_llm.run
    result = enrich_mod.enrich(decl, _domains((domain,)), dry_run=dry_run, use_llm=use_llm, llm=llm, limit=limit,
                                dimensions=dimensions or None)
    by_dim = {d: sum(v.values()) for d, v in (result.get("decided") or {}).items()}
    effect = Effect(summary=f"Write {sum(by_dim.values())} dimension record(s); {result['llm_calls']} LLM call(s).",
                    changes=[Change("mart_record", n, [d]) for d, n in sorted(by_dim.items()) if n],
                    llm_calls=result["llm_calls"], reversible=True,
                    undo="delete the records under curation/mart_dimension/", travels=True, records=[])
    _echo(with_effect(result, effect, dry_run=dry_run), compact)


@mart_group.group("review")
def mart_review() -> None:
    """Review dimension proposals below the confidence threshold."""


@mart_review.command("list")
@click.option("--domain", "domain", required=True)
@compact_option
def mart_review_list(domain, compact):
    rows = records.proposed(domain)
    _echo({"domain": domain, "rows": rows, "rows_total": len(rows), "truncated": False}, compact)


@mart_review.command("set")
@click.option("--domain", "domain", required=True)
@click.option("--dimension", type=click.Choice(["person", "place", "type"]), required=True)
@click.option("--member", "member_key", required=True, help="Entity key (name|CLASS|domain) or type:<value>")
@click.option("--level", "levels", multiple=True, required=True, help="level=value, repeatable")
@dry_run_option("Show the user record that would be written; write nothing")
@compact_option
def mart_review_set(domain, dimension, member_key, levels, dry_run, compact):
    """Write a user record (always wins over rules, seeds and the LLM)."""
    parsed = {}
    for lv in levels:
        if "=" not in lv:
            raise click.ClickException(f"--level needs level=value, got {lv!r}")
        k, v = lv.split("=", 1)
        parsed[k.strip()] = v.strip()
    effect = Effect(summary=f"Write a user record for {member_key}.", changes=[Change("mart_record", 1, [dimension])],
                    reversible=True, undo="write another user record", travels=True, records=[])
    result = {"domain": domain, "dimension": dimension, "member_key": member_key, "levels": parsed}
    if not dry_run:
        result["id"] = records.put_dimension(domain, dimension, member_key, parsed, source="user", confidence=1.0)
    _echo(with_effect(result, effect, dry_run=dry_run), compact)


@mart_group.group("calendar")
def mart_calendar() -> None:
    """Named calendar periods (festivals, holidays) for the time dimension."""


@mart_calendar.command("set")
@click.option("--domain", "domain", required=True)
@click.option("--name", required=True)
@click.option("--from", "start", required=True, help="YYYY-MM-DD")
@click.option("--to", "end", required=True, help="YYYY-MM-DD")
@dry_run_option("Show the period record that would be written; write nothing")
@compact_option
def mart_calendar_set(domain, name, start, end, dry_run, compact):
    effect = Effect(summary=f"Write the period {name!r} {start}..{end}.", changes=[Change("mart_record", 1, ["time"])],
                    reversible=True, undo="delete the record under curation/mart_calendar/", travels=True, records=[])
    result = {"domain": domain, "name": name, "from": start, "to": end}
    if not dry_run:
        result["id"] = records.put_period(domain, name, start, end, source="user")
    _echo(with_effect(result, effect, dry_run=dry_run), compact)


@mart_group.command("build")
@click.option("--domain", "domain", required=True, help="Domain with a mart declaration")
@dry_run_option("Count rows per table from the current graph and records; write nothing")
@compact_option
def mart_build_cmd(domain, dry_run, compact):
    """Build the mart tables into the structured store (no LLM)."""
    from artmind.mart import build as build_mod

    decl = _declaration(domain)
    result = build_mod.build(decl, _domains((domain,)), dry_run=dry_run)
    effect = Effect(summary=f"Write {len(result['rows'])} mart table(s) for {domain}.",
                    changes=[Change("mart_build", sum(result["rows"].values()), sorted(result["rows"]))],
                    reversible=True, undo="artmind mart build again, or delete the mart_* parquet files",
                    travels=False, records=[])
    _echo(with_effect(result, effect, dry_run=dry_run), compact)


@mart_group.command("status")
@click.option("--domain", "domain", required=True)
@compact_option
def mart_status_cmd(domain, compact):
    """Enrichment coverage and unreviewed proposals (read-only)."""
    from artmind.mart import status as status_mod

    _echo(status_mod.report(_declaration(domain), _domains((domain,))), compact)
