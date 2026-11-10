"""text2sql delegated to sql-browser (spec 2026-11-07 §8, Phase B).

artmind keeps the store, the domain scoping and the citations; sql-browser does the
text-to-SQL work (join graph, metadata, pruning, AST guard, self-repair, value
correction). Each call gets its own small DuckDB file holding only the in-scope
`<domain>__<table>` views (never the shared catalog a running console holds open),
the merged metadata for those domains, and artmind's configured LLM passed through
`SQL_BROWSER_*` environment variables. `ARTMIND_TEXT2SQL_ENGINE` picks the engine:
`auto` (default: sql-browser when installed, else built-in), `sql-browser`, `builtin`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

import paths

ENGINES = ("auto", "sql-browser", "builtin")
TIMEOUT_S = 240


class SqlBrowserError(RuntimeError):
    pass


def engine() -> str:
    value = (os.environ.get("ARTMIND_TEXT2SQL_ENGINE") or "auto").strip().lower()
    if value not in ENGINES:
        raise ValueError(f"ARTMIND_TEXT2SQL_ENGINE must be one of {', '.join(ENGINES)}, not {value!r}")
    return value


def executable() -> str | None:
    return shutil.which("sql-browser")


def _work_dir() -> Path:
    d = Path(paths.LOGS_DIR) / "sql"
    d.mkdir(parents=True, exist_ok=True)
    return d


def scoped_db(tables: list[dict]) -> Path:
    """A DuckDB file with views for exactly these tables (reused while they are unchanged)."""
    from artmind.structured.duckdb_adapter import DuckDBDatasource

    stamp = "|".join(sorted(f"{t['domain']}:{t['table_name']}:{t.get('parquet_path')}:{t.get('version')}"
                            for t in tables))
    path = _work_dir() / f"scope-{hashlib.sha256(stamp.encode()).hexdigest()[:16]}.duckdb"
    if not path.exists():
        tmp = path.with_suffix(".duckdb.tmp")
        tmp.unlink(missing_ok=True)
        ds = DuckDBDatasource(tmp)
        ds.ensure_views(tables)
        ds.con.close()
        os.replace(tmp, path)
    return path


def _merge(into: dict, extra: dict) -> None:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(into.get(k), dict):
            _merge(into[k], v)
        elif isinstance(v, list) and isinstance(into.get(k), list):
            into[k] = into[k] + [x for x in v if x not in into[k]]
        elif k not in into:
            into[k] = v


def metadata_for(domains: list[str]) -> Path | None:
    """The domains' metadata merged into one sidecar; authored files win over generated ones."""
    directory = Path(paths.DOMAIN_METADATA_DIR)
    files = []
    for d in domains:
        for name in (f"{d}.meta.yaml", f"{d}.mart.meta.yaml"):
            if (directory / name).is_file():
                files.append(directory / name)
    if not files:
        return None
    merged: dict = {}
    for f in files:
        _merge(merged, yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    text = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
    path = _work_dir() / f"meta-{hashlib.sha256(text.encode()).hexdigest()[:16]}.meta.yaml"
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return path


def llm_env(env: dict) -> dict:
    """SQL_BROWSER_* settings for artmind's configured provider (secrets via environment only)."""
    provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
    if provider == "ibm_ica":
        from artmind.extraction import ibm_ica_client_env

        mapped = ibm_ica_client_env(env)
        return {"SQL_BROWSER_LLM_PROVIDER": "openai", "SQL_BROWSER_LLM_BASE_URL": mapped.get("ARTMIND_KG_LLM_URL", ""),
                "SQL_BROWSER_LLM_API_KEY": mapped.get("ARTMIND_OPENROUTER_API_KEY", "")}
    if provider == "openrouter":
        from artmind.llm_providers import OPENROUTER_BASE_URL

        return {"SQL_BROWSER_LLM_PROVIDER": "openai",
                "SQL_BROWSER_LLM_BASE_URL": env.get("ARTMIND_KG_LLM_URL") or OPENROUTER_BASE_URL,
                "SQL_BROWSER_LLM_API_KEY": env.get("ARTMIND_OPENROUTER_API_KEY", "")}
    return {"SQL_BROWSER_LLM_PROVIDER": "ollama"}


def run(question: str, db: Path, metadata: Path | None, model: str, env: dict, *, dry_run: bool = False) -> dict:
    exe = executable()
    if not exe:
        raise SqlBrowserError("sql-browser is not installed (uv tool install --editable <sql-browser checkout>)")
    cmd = [exe, "query", question, str(db), "--model", model, "--json"]
    cmd += ["--metadata", str(metadata)] if metadata else ["--no-metadata"]
    if dry_run:
        cmd.append("--dry-run")
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SQL_BROWSER_")}
    child_env.update(llm_env(env))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=child_env,
                              cwd=str(_work_dir()))
    except subprocess.TimeoutExpired as e:
        raise SqlBrowserError(f"sql-browser timed out after {TIMEOUT_S}s") from e
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as e:
        raise SqlBrowserError(f"sql-browser returned no JSON (exit {proc.returncode}): {proc.stderr.strip()[-300:]}") from e
    if proc.returncode != 0 or payload.get("error"):
        raise SqlBrowserError(payload.get("error") or proc.stderr.strip()[-300:] or f"exit {proc.returncode}")
    return payload


def metadata_path(domain: str) -> Path:
    return Path(paths.DOMAIN_METADATA_DIR) / f"{domain}.meta.yaml"


def metadata_command(sub: str, domain: str, tables: list[dict], env: dict, *, model: str | None = None,
                     llm_draft: bool = False) -> dict:
    """Run `sql-browser metadata init|validate|regenerate` on the domain's tables and its sidecar."""
    exe = executable()
    if not exe:
        raise SqlBrowserError("sql-browser is not installed (uv tool install --editable <sql-browser checkout>)")
    if not tables:
        raise SqlBrowserError(f"no structured tables in {domain!r}")
    target = metadata_path(domain)
    cmd = [exe, "metadata", sub, str(scoped_db(tables))]
    if sub == "init":
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--out", str(target)] + (["--llm-draft"] if llm_draft else [])
    else:
        if not target.is_file():
            raise SqlBrowserError(f"no metadata file yet: {target} (run artmind db metadata init --domain {domain})")
        cmd += ["--metadata", str(target)]
    if model and (sub == "regenerate" or llm_draft):
        cmd += ["--model", model]
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SQL_BROWSER_")}
    child_env.update(llm_env(env))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S * 4, env=child_env,
                          cwd=str(_work_dir()))
    return {"path": str(target), "exit_code": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip()[-2000:], "ok": proc.returncode == 0}
