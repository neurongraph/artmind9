"""Lane B JSON endpoints for the admin ingest dashboard.

Deterministic (no LLM in the loop) — plain wrappers around `artmind.jobs`,
`artmind.ingest`, and `artmind.graph_query`. Mounted only on the admin app
(`create_app(..., admin_routes=True)` / `run_admin_ui`), never the Q&A app.
"""
import asyncio
import io
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import click
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from artmind.cli import _ensure_worker_running, _get_available_domains
from artmind.graph_query import structural_metadata
from artmind.ingest import _build_file_result_from_db, commit_to_graph, embed_entities_backfill, extract_kg
from artmind.jobs import (
    _create_job,
    _fetch_active_jobs,
    _fetch_chunks,
    _fetch_completed_jobs,
    _get_job_results,
    _get_job_status,
    _list_jobs,
    _retry_job,
)
from artmind.kg_pull import pull_kg as pull_kg_fn
from artmind.structured import STRUCTURED_EXTENSIONS
from artmind.structured import registry as structured_registry
from artmind.structured.duckdb_adapter import DuckDBDatasource
from artmind.structured.pipeline import ingest_structured_file
from artmind.text2sql import validate_read_only_sql
from artmind.unified_snapshot import analyze_snapshot, create_snapshot, restore_snapshot_impl
from artmind.webui.help import get_concepts
from paths import GRAPH_SNAPSHOT_DIR, KG_DIR, STRUCTURED_SNAPSHOT_DIR
from utils.functions import load_env, resolve_llm_model


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(p.capitalize() for p in rest)


def _camelize(obj):
    if isinstance(obj, dict):
        return {_camel(k): _camelize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_camelize(v) for v in obj]
    return obj


class IngestRequest(BaseModel):
    domain: str
    path: str
    stage_only: bool = Field(False, alias="stageOnly")

    model_config = {"populate_by_name": True}


class RetryRequest(BaseModel):
    include_skipped: bool = Field(False, alias="includeSkipped")

    model_config = {"populate_by_name": True}


class EmbedEntitiesRequest(BaseModel):
    domain: str


class ResumeExtractRequest(BaseModel):
    domain: str


class PullKgRequest(BaseModel):
    repo: str
    repo_path: str = Field(alias="repoPath")
    domain: str

    model_config = {"populate_by_name": True}


class RestoreRequest(BaseModel):
    confirm: bool = False
    components: list[str] = Field(default_factory=lambda: ["graph", "registry", "structured", "kg_staging"])


class StructuredSqlRequest(BaseModel):
    sql: str


def _json_len(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return 0


def _validate_artifact_segment(value: str) -> None:
    """Reject a domain/doc value that isn't a single, safe filesystem path segment.

    Used wherever a route builds a filesystem path as `KG_DIR / domain [/ doc]`
    from request input. FastAPI's default path converter only forbids literal
    `/` in a path parameter — it still accepts values like `".."` — and query/
    form fields have no such restriction at all, so this must be checked
    explicitly before the value touches a path.
    """
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid domain/doc value")


def _safe_snapshot_path(name: str, snapshot_dir: Path | None = None) -> Path:
    """Validate a snapshot filename and return its safe path."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid snapshot name")
    if snapshot_dir is None:
        snapshot_dir = GRAPH_SNAPSHOT_DIR
    path = (snapshot_dir / name).resolve()
    safe_dir = snapshot_dir.resolve()
    if safe_dir != path.parent:
        raise HTTPException(status_code=400, detail="Invalid snapshot name")
    return path


def _run_structured_sql(sql: str) -> list[dict]:
    ds = DuckDBDatasource()
    ds.ensure_views(structured_registry.list_tables())
    return ds.run_sql(sql)


def register_dashboard_routes(app: FastAPI, templates: Jinja2Templates) -> FastAPI:
    @app.get("/dashboard")
    async def dashboard_page(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {})

    @app.get("/api/cli-guide", response_class=HTMLResponse)
    async def api_cli_guide():
        """The CLI guide as an HTML fragment, injected into the dashboard's CLI guide tab.

        Generated fresh from this process's live `artmind.cli` import on every
        request (no file on disk involved) — always in sync with whatever
        `cli.py` this admin-ui process started with. Note it can still go
        stale relative to *uncommitted* edits until admin-ui is restarted,
        same as the `serve` daemon (see CLAUDE.md).
        """
        from artmind.cli_guide import render_fragment

        return HTMLResponse(content=render_fragment(), headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs")
    async def api_list_jobs(status: str | None = None):
        return _camelize(_list_jobs(status_filter=status))

    @app.get("/api/jobs/active")
    async def api_jobs_active():
        return _camelize(_fetch_active_jobs())

    @app.get("/api/jobs/completed")
    async def api_jobs_completed(limit: int = 100):
        return _camelize(_fetch_completed_jobs(limit=limit))

    @app.get("/api/jobs/{job_id}")
    async def api_job_status(job_id: str):
        result = _get_job_status(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return _camelize(result)

    @app.get("/api/jobs/{job_id}/results")
    async def api_job_results(job_id: str):
        result = _get_job_results(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return _camelize(result)

    @app.post("/api/jobs/{job_id}/retry")
    async def api_retry_job(job_id: str, payload: RetryRequest):
        try:
            result = _retry_job(job_id, include_skipped=payload.include_skipped)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result["retried"]:
            _ensure_worker_running()
        return _camelize(result)

    @app.post("/api/ingest")
    async def api_ingest(payload: IngestRequest):
        _validate_artifact_segment(payload.domain)
        path = Path(payload.path)
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"Path not found: {payload.path}")
        files = sorted(f for f in (path.rglob("*") if path.is_dir() else [path]) if f.is_file())
        if not files:
            raise HTTPException(status_code=400, detail=f"No files found in {payload.path}")
        batch_files = [str(f.resolve()) for f in files]
        job_id = _create_job(batch_files, domain=payload.domain, stage_only=payload.stage_only)
        _ensure_worker_running()
        return _camelize({
            "job_id": job_id,
            "domain": payload.domain,
            "file_count": len(batch_files),
        })

    @app.get("/api/jobs/{job_id}/chunks")
    async def api_job_chunks(job_id: str, doc: str):
        job = _get_job_status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        file_result = _build_file_result_from_db(doc, job["domain"])
        if file_result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Document '{doc}' not found in registry for domain '{job['domain']}'",
            )
        return _camelize(_fetch_chunks(file_result["sha256"]))

    @app.post("/api/documents/{doc}/resume-extract")
    async def api_resume_extract(doc: str, payload: ResumeExtractRequest):
        _validate_artifact_segment(doc)
        _validate_artifact_segment(payload.domain)
        file_result = _build_file_result_from_db(doc, payload.domain)
        if file_result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Document '{doc}' not found in registry for domain '{payload.domain}'",
            )
        if file_result["chunk_count"] == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No chunks found at {file_result['chunks_dir']} — run 'ingest sync' first",
            )
        env = load_env()
        text_model = resolve_llm_model(env)
        embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
        doc_kg_dir = await asyncio.to_thread(extract_kg, file_result, payload.domain, text_model, embed_model)
        if doc_kg_dir is None:
            raise HTTPException(status_code=400, detail="extract_kg failed — check logs for details")
        return {"doc": doc, "domain": payload.domain, "kgDir": str(doc_kg_dir)}

    @app.get("/api/artifacts")
    async def api_artifacts(domain: str):
        _validate_artifact_segment(domain)
        domain_dir = KG_DIR / domain
        if not domain_dir.exists():
            return []

        in_graph_names: set[str] = set()
        try:
            meta = structural_metadata([domain])
            for row in meta.get("rows", []):
                if row.get("label") == "Document":
                    in_graph_names = {n.upper() for n in (row.get("names") or [])}
        except Exception:
            pass

        artifacts = []
        for doc_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir()):
            doc_json = doc_dir / "document.json"
            if not doc_json.exists():
                continue
            try:
                document = json.loads(doc_json.read_text(encoding="utf-8"))
            except Exception:
                document = {}
            name = document.get("name", doc_dir.name)
            artifacts.append({
                "doc": doc_dir.name,
                "name": name,
                "entity_count": _json_len(doc_dir / "entities.json"),
                "property_count": _json_len(doc_dir / "properties.json"),
                "relationship_count": _json_len(doc_dir / "relationships.json"),
                "in_graph": name.upper() in in_graph_names,
            })
        return _camelize(artifacts)

    @app.get("/api/artifacts/{domain}/{doc}/bundle")
    async def api_artifact_bundle(domain: str, doc: str):
        _validate_artifact_segment(domain)
        _validate_artifact_segment(doc)
        doc_dir = KG_DIR / domain / doc
        if not doc_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"KG folder not found: {domain}/{doc}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in doc_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=f.relative_to(doc_dir))
        buf.seek(0)
        # macOS Archive Utility truncates the extracted folder name at the
        # *first* dot in the zip's filename (treating the rest as a compound
        # extension) — e.g. "banking.cases_foo.zip" unzips into a folder
        # named just "banking". Swap dots in the domain segment only, so a
        # dotted domain (e.g. "banking.cases") can't trigger this.
        safe_name = f"{domain.replace('.', '_')}_{doc}.zip"
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    @app.post("/api/artifacts/import")
    async def api_artifact_import(domain: str = Form(...), doc: str = Form(...), file: UploadFile = File(...)):
        _validate_artifact_segment(domain)
        _validate_artifact_segment(doc)
        dest_dir = KG_DIR / domain / doc
        dest_dir.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                dest_resolved = dest_dir.resolve()
                for member in zf.infolist():
                    member_path = (dest_dir / member.filename).resolve()
                    if dest_resolved != member_path and dest_resolved not in member_path.parents:
                        raise HTTPException(status_code=400, detail=f"Unsafe zip entry: {member.filename}")
                zf.extractall(dest_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
        ok = await asyncio.to_thread(commit_to_graph, dest_dir, domain)
        if not ok:
            raise HTTPException(status_code=400, detail="write_to_graph failed — check logs for Neo4j errors")
        return {"domain": domain, "doc": doc, "written": True}

    @app.post("/api/artifacts/{domain}/{doc}/write-to-graph")
    async def api_artifact_commit(domain: str, doc: str):
        _validate_artifact_segment(domain)
        _validate_artifact_segment(doc)
        doc_dir = KG_DIR / domain / doc
        if not (doc_dir / "document.json").exists():
            raise HTTPException(status_code=404, detail=f"Staged KG not found: {domain}/{doc}")
        ok = await asyncio.to_thread(commit_to_graph, doc_dir, domain)
        if not ok:
            raise HTTPException(status_code=400, detail="commit_to_graph failed — check logs for Neo4j errors")
        return {"domain": domain, "doc": doc, "written": True}

    @app.post("/api/artifacts/pull")
    async def api_artifacts_pull(payload: PullKgRequest):
        _validate_artifact_segment(payload.domain)
        try:
            result = await asyncio.to_thread(pull_kg_fn, payload.repo, payload.repo_path, payload.domain)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _camelize(result)

    # ── unified snapshots (graph, registry, structured, kg_staging) ────────────

    @app.get("/api/snapshots")
    async def api_list_snapshots():
        """List all unified snapshots (.zip files)."""
        if not GRAPH_SNAPSHOT_DIR.exists():
            return []
        snapshots = []
        for path in sorted(GRAPH_SNAPSHOT_DIR.glob("artmind_snapshot_*.zip"), reverse=True):
            stat = path.stat()
            try:
                analysis = await asyncio.to_thread(analyze_snapshot, path, include=None)
                snapshots.append({
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "components": analysis.get("available_components", []),
                })
            except Exception:
                # Skip snapshots that can't be read
                pass
        return _camelize(snapshots)

    @app.post("/api/snapshots")
    async def api_create_snapshot(components: str = "graph,registry,structured,kg_staging"):
        """Create a unified snapshot with selected components."""
        try:
            component_set = set(c.strip() for c in components.split(",") if c.strip())
            valid = {"graph", "registry", "structured", "kg_staging"}
            if not component_set or not component_set <= valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid components. Choose from: {', '.join(sorted(valid))}"
                )
            path = await asyncio.to_thread(create_snapshot, component_set)
            stat = path.stat()
            return _camelize({
                "name": path.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "components": sorted(component_set),
            })
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/snapshots/{name}")
    async def api_download_snapshot(name: str):
        """Download a snapshot zip file."""
        path = _safe_snapshot_path(name, GRAPH_SNAPSHOT_DIR)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {name}")
        return FileResponse(path, media_type="application/zip", filename=name)

    @app.post("/api/snapshots/{name}/analyze")
    async def api_analyze_snapshot(name: str, components: str | None = None):
        """Analyze a snapshot and show what would be restored (with stale warnings)."""
        path = _safe_snapshot_path(name, GRAPH_SNAPSHOT_DIR)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {name}")
        try:
            component_set = None
            if components:
                component_set = set(c.strip() for c in components.split(",") if c.strip())
            analysis = await asyncio.to_thread(analyze_snapshot, path, include=component_set)
            return _camelize(analysis)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/snapshots/{name}/restore")
    async def api_restore_snapshot(name: str, payload: RestoreRequest):
        """Restore selected components from a snapshot."""
        if not payload.confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true to restore")
        path = _safe_snapshot_path(name, GRAPH_SNAPSHOT_DIR)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Snapshot not found: {name}")
        try:
            component_set = set(payload.components) if payload.components else None
            result = await asyncio.to_thread(restore_snapshot_impl, path, component_set)
            return _camelize(result)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/snapshots/import")
    async def api_import_snapshot(
        file: UploadFile = File(...),
        components: str = "graph,registry,structured,kg_staging",
        confirm: bool = Form(False),
    ):
        """Upload and restore a snapshot."""
        if not confirm:
            raise HTTPException(status_code=400, detail="Set confirm=true to restore")
        try:
            GRAPH_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            dest = GRAPH_SNAPSHOT_DIR / Path(file.filename).name
            content = await file.read()
            dest.write_bytes(content)

            component_set = set(c.strip() for c in components.split(",") if c.strip())
            result = await asyncio.to_thread(restore_snapshot_impl, dest, component_set)
            return _camelize(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/help/concepts")
    async def api_help_concepts():
        return _camelize(get_concepts())

    @app.get("/api/domains")
    async def api_domains():
        return _get_available_domains()

    @app.get("/api/stats")
    async def api_stats(domain: str):
        domains = [d.strip() for d in domain.split(",") if d.strip()]
        return _camelize(structural_metadata(domains))

    @app.post("/api/embed-entities")
    async def api_embed_entities(payload: EmbedEntitiesRequest):
        return _camelize(embed_entities_backfill(payload.domain))

    # ── structured data tab (Lane B) ──────────────────────────────────
    @app.get("/api/structured/tables")
    async def api_structured_tables(domain: str | None = None):
        domains = [d.strip() for d in domain.split(",") if d.strip()] if domain else None
        return _camelize({"tables": structured_registry.list_tables(domains)})

    @app.get("/api/structured/tables/{table}/schema")
    async def api_structured_table_schema(table: str, domain: str | None = None):
        row = structured_registry.get_table(table, domain=domain)
        if row is None:
            raise HTTPException(status_code=404, detail=f"table '{table}' not found")
        columns = structured_registry.get_columns(row["id"])
        mappings = structured_registry.list_mappings(row["id"])
        return _camelize({**row, "columns": columns, "mappings": mappings})

    @app.post("/api/structured/ingest")
    async def api_structured_ingest(
        domain: str = Form(...),
        file: UploadFile = File(...),
        table: str | None = Form(None),
        sheet: str | None = Form(None),
        header_row: int = Form(0, alias="headerRow"),
        force: bool = Form(False),
    ):
        _validate_artifact_segment(domain)
        suffix = Path(file.filename).suffix.lower()
        if suffix not in STRUCTURED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}")
        content = await file.read()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            try:
                resolved_table = table or Path(file.filename).stem
                result = await asyncio.to_thread(
                    ingest_structured_file, tmp_path, domain,
                    table=resolved_table, sheet=sheet, header_row=header_row, force=force,
                )
            except click.ClickException as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        return _camelize(result)

    @app.post("/api/structured/sql")
    async def api_structured_sql(payload: StructuredSqlRequest):
        try:
            validate_read_only_sql(payload.sql)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            rows = await asyncio.to_thread(_run_structured_sql, payload.sql)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"SQL error: {exc}") from exc
        return {"query_type": "sql", "command": "db sql", "rows": rows}


    return app
