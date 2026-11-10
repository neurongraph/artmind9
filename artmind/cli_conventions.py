"""The CLI's output and option conventions, in one place (spec 2026-11-02-cli-uniformity-design.md).

Stdlib + Click only, so `cli.py` and the contract tests can both import it.

- C1/C4 the result envelope: `envelope()` gives every JSON result `command`,
  `warnings` (list) and `next` (string or null); `compact_option` is the shared
  `--compact` flag.
- C2 one spelling per option, camelCase: `aliased_option()` renames an option
  and keeps the old spelling working as a hidden alias with one stderr note.
- C3 `--dryRun` and the `effect` block on every write: `dry_run_option`,
  `Effect`, and `WRITE_COMMANDS` (the contract test's list of writers).
- C6 exit codes: the `EXIT_*` constants.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import click

# ── exit codes (C6) ──────────────────────────────────────────────────────────

EXIT_OK = 0
#: Bad input the user can fix: unknown id, invalid YAML, a refused precondition.
EXIT_INPUT = 1
#: Usage: unknown option, missing argument (Click's own code).
EXIT_USAGE = 2
#: A store is unreachable: Neo4j, the structured store, the embedding service, the LLM.
EXIT_STORE = 3

# ── command classes ──────────────────────────────────────────────────────────

#: Long-running servers: no JSON result, exempt from C1.
#: Command path -> the payload key holding its main list, cut first when `ARTMIND_OUTPUT_BUDGET`
#: is exceeded (artmind/result_store.py). A command not listed falls back to the first of
#: documents / results / rows present in its payload.
LIST_KEY: dict[str, str] = {
    "query survey": "documents",
    "query topics run": "rows",
    "query vector-text": "rows",
    "query entity-resolve": "rows",
    "query page": "rows",
    "query graph pattern1": "rows",
    "query graph pattern2": "rows",
    "query graph pattern7": "rows",
    "query entity-context": "rows",
}

SERVER_COMMANDS = frozenset({"serve", "chat-ui", "admin-ui"})
#: Hands the terminal to a SQL shell: exempt from C1.
INTERACTIVE_COMMANDS = frozenset({"db connect"})

#: Command path (space-separated) -> tier. Tier 1 has a real `--dryRun` with an
#: `effect` block (this spec); tier 2 is listed so a later spec builds it, and
#: its `--dryRun` refuses to run (`unbuilt_dry_run_option`) until then.
WRITE_COMMANDS: dict[str, int] = {
    **{p: 1 for p in (
        "sameas approve", "sameas reject",
        "ingest resolve-conflict", "ingest supersede", "ingest unsupersede", "ingest sync", "ingest table2graph",
        "ingest retime", "ingest summarise",
        "docs retire", "docs restore", "docs archive",
        "projection rebuild", "projection synthesize",
        "update confirm", "update retract",
        "views save", "views delete",
        "topic build", "topic refresh", "topic delete",
        "vault sync", "traces prune", "catalogue refresh",
    )},
    **{p: 2 for p in (
        "db grain", "db mappings set", "db mappings confirm", "db mappings clear",
        "db bridge confirm", "db bridge clear", "db propose", "db refresh",
        "db restore", "db restore-text",
        "docs reindex", "docs restore-from-archive",
        "ingest write-to-graph", "ingest pull-kg", "ingest async", "ingest retry-job",
        "ingest embed-entities", "ingest embed-passages",
        "snapshot restore", "review new", "review attach", "insights refresh",
        "domains add", "domains delete", "domains harmonize",
        "vault migrate-frontmatter",
    )},
}

# ── C6: exit code 3, a store is unreachable ──────────────────────────────────

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")


def _redact(text: str) -> str:
    """Drop `user:password@` from any URL in `text`."""
    return _URL_CREDENTIALS.sub(r"\g<scheme>", text)


def _host_port(uri: str) -> str:
    """`neo4j+s://user:pw@host:7687/db` -> `host:7687`; anything unparseable -> ''."""
    netloc = urlsplit(uri if "://" in uri else f"//{uri}").netloc
    return netloc.rsplit("@", 1)[-1]


def _first_line(exc: BaseException) -> str:
    text = _redact(str(exc)).strip().splitlines()
    return text[0] if text else type(exc).__name__


def store_failure(exc: BaseException) -> tuple[str, str, str] | None:
    """`(store, address, reason)` if `exc` is a connection failure to a store, else None.

    Only connection-level errors count: Neo4j `ServiceUnavailable`, `AuthError` and
    `SessionExpired`; DuckDB connection/IO errors; the embedding and LLM clients'
    connection errors. A query error, a bad answer or a bug is not a store failure.
    The address is host and port only, never credentials.
    """
    try:
        from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired
    except ImportError:  # pragma: no cover - neo4j is a core dependency
        AuthError = ServiceUnavailable = SessionExpired = ()  # type: ignore[assignment]
    if isinstance(exc, (ServiceUnavailable, AuthError, SessionExpired)):
        uri = os.environ.get("ARTMIND_KG_NEO4J_URI") or ""
        if not uri:
            try:
                from utils.functions import load_env

                uri = load_env().get("ARTMIND_KG_NEO4J_URI") or ""
            except Exception:
                uri = ""
        return "neo4j", _host_port(uri) or "the configured address", _first_line(exc)
    try:
        import duckdb

        if isinstance(exc, (duckdb.IOException, duckdb.ConnectionException)):
            import paths

            return "structured store", str(paths.STRUCTURED_DIR), _first_line(exc)
    except ImportError:  # pragma: no cover - duckdb is a core dependency
        pass
    connection_errors: list[type[BaseException]] = []
    for module in ("openai", "anthropic"):
        try:
            connection_errors.append(importlib.import_module(module).APIConnectionError)
        except (ImportError, AttributeError):
            continue
    if connection_errors and isinstance(exc, tuple(connection_errors)):
        request = getattr(exc, "request", None)
        url = str(getattr(request, "url", "") or "")
        return "LLM or embedding service", _host_port(url) or "the configured endpoint", _first_line(exc)
    return None


def exit_on_store_failure(exc: Exception, ctx: click.Context) -> None:
    """Print the one-line note and exit 3 when `exc` is a store failure; return otherwise."""
    failure = store_failure(exc)
    if failure is None:
        return
    store, address, reason = failure
    click.echo(f"artmind: {store} unreachable at {address}: {reason}", err=True)
    ctx.exit(EXIT_STORE)


# ── C2: renamed options ──────────────────────────────────────────────────────

#: Last release that still accepts the old spellings; named in the stderr note.
ALIASES_REMOVED_AFTER = "0.11"

#: Old spelling -> new spelling. The contract test fails while a caller in the
#: checkout (skills, justfile, routes.yaml, plugin, living docs) uses a key.
DEPRECATED_ALIASES: dict[str, str] = {
    "--dry-run": "--dryRun",
    "--stage-only": "--stageOnly",
    "--from-file": "--fromFile",
    "--repo-path": "--repoPath",
    "--allow-cross-domain-merge": "--allowCrossDomainMerge",
    "--include-skipped": "--includeSkipped",
    "--no-narrate": "--noNarrate",
    "--min-score": "--minScore",
    "--rebuild-projection": "--rebuildProjection",
    "--acp-cmd": "--acpCmd",
    "--base-url": "--baseUrl",
}


class AliasedOption(click.Option):
    """An option whose old spellings still parse but are hidden from help.

    Each old spelling is registered with the parser under its own destination,
    so a use can be told apart from the new spelling; `consume_value` folds the
    value into the real parameter and writes one stderr line naming the new one.
    """

    def __init__(self, *args: Any, aliases: tuple[str, ...] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.aliases = tuple(aliases)

    def _alias_dest(self, index: int) -> str:
        return f"{self.name}__alias{index}"

    def add_to_parser(self, parser: Any, ctx: click.Context) -> None:
        super().add_to_parser(parser, ctx)
        if self.multiple:
            action = "append"
        elif self.is_flag:
            action = "store_const"
        else:
            action = "store"
        for index, alias in enumerate(self.aliases):
            parser.add_option(
                obj=self, opts=[alias], dest=self._alias_dest(index), action=action,
                nargs=self.nargs, const=self.flag_value if self.is_flag else None,
            )

    def consume_value(self, ctx: click.Context, opts: Any) -> Any:
        for index, alias in enumerate(self.aliases):
            dest = self._alias_dest(index)
            if dest not in opts:
                continue
            if self.name not in opts:
                opts = {**opts, self.name: opts[dest]}
            if not ctx.resilient_parsing:
                new = DEPRECATED_ALIASES.get(alias, self.opts[0])
                click.echo(
                    f"artmind: {alias} is now {new}; the old spelling is removed after {ALIASES_REMOVED_AFTER}",
                    err=True,
                )
        return super().consume_value(ctx, opts)


def aliased_option(new: str, *old: str, **kwargs: Any):
    """`click.option(new, ...)` that also accepts `old` spellings as hidden aliases.

    Name the Python parameter with `param`:
    `aliased_option("--stageOnly", "--stage-only", param="stage_only", is_flag=True)`.
    """
    param = kwargs.pop("param", None)
    names = (new, param) if param else (new,)
    return click.option(*names, cls=AliasedOption, aliases=tuple(old), **kwargs)


def compact_option(func):
    """The shared `--compact` flag."""
    return click.option("--compact", is_flag=True, help="Emit compact JSON")(func)


#: Commands that must take `--shape brief|full` (contract test walks these).
SHAPE_COMMANDS = frozenset({
    "query survey", "query vector-text", "query chunks", "query entity-context",
    "query graph pattern1", "query graph pattern2", "query graph pattern7", "query graph pattern8",
    "query insights show",
})

SHAPE_HELP = ("brief: short rows (id, title, domain, date, status, extractive gist) for an agent; "
              "full: every key. Changes row content; independent of --compact, which only "
              "changes serialisation")


def shape_option(func):
    """The shared `--shape brief|full` option (default full)."""
    return click.option("--shape", type=click.Choice(["brief", "full"]), default="full",
                        show_default=True, help=SHAPE_HELP)(func)


def dry_run_option(help: str = "Report what would change; write nothing", *, legacy: bool = True):
    """`--dryRun`. `legacy=True` keeps the old `--dry-run` spelling as a hidden alias."""
    return aliased_option(
        "--dryRun", *(("--dry-run",) if legacy else ()), param="dry_run", is_flag=True, help=help,
    )


def unbuilt_dry_run_option(func):
    """A tier-2 `--dryRun`: present so the contract holds, but it refuses to run.

    A flag that did nothing would let a write go ahead under a name that promises
    it will not, so asking for it fails before the command acts.
    """

    def _refuse(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
        if value and not ctx.resilient_parsing:
            raise click.ClickException(
                f"--dryRun is not available for `{_command_path(ctx)}` yet; nothing was changed"
            )
        return value

    return click.option(
        "--dryRun", "dry_run", is_flag=True, expose_value=False, is_eager=True, callback=_refuse,
        help="Not available yet for this command: fails without acting.",
    )(func)


# ── C1/C4: the envelope ──────────────────────────────────────────────────────


def _command_path(ctx: click.Context | None) -> str | None:
    """The canonical Click path below the root, space-separated."""
    if ctx is None:
        return None
    names: list[str] = []
    cur: click.Context | None = ctx
    while cur is not None and cur.parent is not None:
        if cur.info_name:
            names.append(cur.info_name)
        cur = cur.parent
    return " ".join(reversed(names)) or None


def envelope(payload: Any, ctx: click.Context | None = None) -> Any:
    """Put `command`, `warnings` and `next` first; every other key follows untouched."""
    if not isinstance(payload, dict):
        return payload
    warnings = payload.get("warnings")
    if warnings is None:
        warnings = []
    elif not isinstance(warnings, list):
        warnings = [warnings]
    out: dict[str, Any] = {
        "command": _command_path(ctx) if ctx is not None else payload.get("command"),
        "warnings": warnings,
        "next": payload.get("next"),
    }
    for key, value in payload.items():
        if key not in out:
            out[key] = value
    return out


# ── C3: the effect block ─────────────────────────────────────────────────────

#: The fixed vocabulary of `changes[].kind`. A new kind is added here, with a test.
EFFECT_KINDS = frozenset({
    "entity_create", "entity_merge", "entity_unmerge", "entity_delete", "entity_change",
    "observation_move", "document_ingest", "document_reextract", "document_retire",
    "document_restore", "document_supersede", "document_unsupersede", "conflict_resolve", "conflict_dismiss",
    "summary_write", "description_write", "view_save", "view_delete", "topic_write",
    "topic_delete", "table_change", "trace_delete", "fact_write", "fact_retract",
    "proposal_reject", "projection_rebuild", "document_archive", "catalogue_refresh",
})

SAMPLE_CAP = 10


@dataclass
class Change:
    kind: str
    count: int
    sample: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in EFFECT_KINDS:
            raise ValueError(f"unknown effect kind {self.kind!r}; add it to cli_conventions.EFFECT_KINDS")

    def to_json(self) -> dict:
        return {"kind": self.kind, "count": self.count, "sample": list(self.sample)[:SAMPLE_CAP]}


@dataclass
class Effect:
    """What a write did, or with `--dryRun` would do (spec §3.4)."""

    summary: str
    changes: list[Change] = field(default_factory=list)
    stale_topics: list[str] = field(default_factory=list)
    stale_views: list[str] = field(default_factory=list)
    llm_calls: int = 0
    llm_model: str | None = None
    est_cost_usd: float | None = None
    reversible: bool = False
    undo: str | None = None
    travels: bool = False
    records: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "summary": self.summary,
            "changes": [c.to_json() for c in self.changes],
            "stale": {"topics": list(self.stale_topics), "views": list(self.stale_views)},
            "llm": {"calls": self.llm_calls, "model": self.llm_model, "est_cost_usd": self.est_cost_usd},
            "reversible": self.reversible,
            "undo": self.undo,
            "travels": self.travels,
            "records": list(self.records),
        }


def with_effect(payload: dict, effect: Effect, dry_run: bool = False) -> dict:
    """`payload` plus its `effect`; with a dry run also `"dry_run": true`."""
    out = {**payload, "effect": effect.to_json()}
    if dry_run:
        out["dry_run"] = True
    return out
