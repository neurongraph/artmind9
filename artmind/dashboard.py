"""Textual-based ingestion dashboard.

Layout:
  Tab 1 (Active Jobs)  – left Tree (jobs/files) + right scrollable detail panel
  Tab 2 (Completed)    – DataTable with file-level summary rows

Detail panel refreshes from DB on every tree navigation move.
R key rebuilds the full tree and resets focus to the first job.
"""
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    RichLog,
    Static,
    TabPane,
    TabbedContent,
    Tree,
)

from artmind.db import _get_db
from paths import WORKER_LOG


# ── Data layer ────────────────────────────────────────────────────────────────


def _fetch_active_jobs() -> list[dict]:
    """Return queued/processing jobs with their file rows."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, domain"
            " FROM ingestion_jobs WHERE status IN ('queued','processing')"
            " ORDER BY queued_at DESC LIMIT 20"
        ).fetchall()
        result = []
        for row in rows:
            job_id = row[0]
            files = conn.execute(
                "SELECT filename, status, current_step, doc_sha256"
                " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            result.append({
                "job_id": job_id,
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
                "started_at": row[5],
                "domain": row[6] or "general",
                "files": [
                    {"filename": f[0], "status": f[1], "current_step": f[2], "doc_sha256": f[3]}
                    for f in files
                ],
            })
        return result
    finally:
        conn.close()


def _fetch_completed_jobs(limit: int = 100) -> list[dict]:
    """Return completed/failed jobs with per-file summary rows."""
    conn = _get_db()
    try:
        jobs = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, completed_at, domain, error_message"
            " FROM ingestion_jobs WHERE status IN ('completed','failed')"
            " ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in jobs:
            job_id = row[0]
            files = conn.execute(
                "SELECT filename, status, error_message, started_at, completed_at"
                " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            result.append({
                "job_id": job_id,
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
                "started_at": row[5],
                "completed_at": row[6],
                "domain": row[7] or "general",
                "error_message": row[8],
                "files": [
                    {
                        "filename": f[0],
                        "status": f[1],
                        "error_message": f[2],
                        "started_at": f[3],
                        "completed_at": f[4],
                    }
                    for f in files
                ],
            })
        return result
    finally:
        conn.close()


def _fetch_job(job_id: str) -> dict | None:
    """Return fresh status for a single job, or None if not found."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, domain"
            " FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        files = conn.execute(
            "SELECT filename, status, current_step, doc_sha256"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        ).fetchall()
        return {
            "job_id": row[0],
            "status": row[1],
            "file_count": row[2],
            "processed_count": row[3],
            "queued_at": row[4],
            "started_at": row[5],
            "domain": row[6] or "general",
            "files": [
                {"filename": f[0], "status": f[1], "current_step": f[2], "doc_sha256": f[3]}
                for f in files
            ],
        }
    finally:
        conn.close()


def _fetch_file_entry(job_id: str, filename: str) -> dict | None:
    """Return fresh status for a single file row, or None if not found."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT filename, status, current_step, doc_sha256"
            " FROM ingestion_job_files WHERE job_id = ? AND filename = ?",
            (job_id, filename),
        ).fetchone()
        if not row:
            return None
        return {"filename": row[0], "status": row[1], "current_step": row[2], "doc_sha256": row[3]}
    finally:
        conn.close()


def _fetch_chunks(doc_sha256: str) -> list[dict]:
    """Return per-chunk KG extraction status for a document."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT chunk_seq, entities_status, properties_status, relationships_status"
            " FROM kg_chunk_status WHERE doc_sha256 = ? ORDER BY chunk_seq",
            (doc_sha256,),
        ).fetchall()
        return [{"seq": r[0], "e": r[1], "p": r[2], "r": r[3]} for r in rows]
    finally:
        conn.close()


# ── Log helpers ──────────────────────────────────────────────────────────────


def _read_log_tail(n: int = 100) -> list[str]:
    if not WORKER_LOG.exists():
        return []
    try:
        lines = WORKER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        # The worker has two log handlers; the file handler emits millisecond timestamps
        # (position 19 == '.') while the stderr-redirected copy does not — keep one.
        lines = [l for l in lines if len(l) > 19 and l[19] == "."]
        return lines[-n:]
    except Exception:
        return []


def _log_line_style(line: str) -> str:
    if "[ERROR" in line:
        return "red3"
    if "[WARNING" in line:
        return "yellow"
    if "[DEBUG" in line:
        return "dim"
    return ""


# ── Rendering helpers ─────────────────────────────────────────────────────────

_STATUS_COLOR = {
    "queued": "yellow",
    "processing": "dodger_blue1",
    "completed": "green3",
    "failed": "red3",
    "ok": "green3",
    "skipped": "cyan",
    "pending": "grey50",
    "error": "red3",
}
_STATUS_ICON = {
    "queued": "○",
    "processing": "◐",
    "completed": "●",
    "failed": "✗",
    "ok": "●",
    "skipped": "◌",
    "pending": "○",
    "error": "✗",
}


def _fmt(ts: str | None, fmt: str = "%H:%M:%S") -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromisoformat(ts).strftime(fmt)
    except Exception:
        return ts


def _icon(status: str) -> str:
    return f"[{_STATUS_COLOR.get(status, 'white')}]{_STATUS_ICON.get(status, '?')}[/{_STATUS_COLOR.get(status, 'white')}]"


def _progress_bar(label: str, done: int, total: int, width: int = 16) -> str:
    if total == 0:
        return f"{label}: [dim]—[/dim]"
    filled = int(done / total * width)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(done / total * 100)
    color = "green3" if done == total else "dodger_blue1"
    return f"{label}: [{color}]{bar}[/{color}] [dim]{done}/{total} ({pct}%)[/dim]"


# ── Widgets ───────────────────────────────────────────────────────────────────


class DetailPanel(Static):
    """Right-side panel: shows chunk status for a selected file, or a job summary."""

    DEFAULT_CSS = "DetailPanel { padding: 1 2; height: auto; }"

    def show_placeholder(self) -> None:
        self.update("[dim]← Select a job or file from the tree[/dim]")

    def show_job(self, job: dict) -> None:
        c = _STATUS_COLOR.get(job["status"], "white")
        lines = [
            f"[bold]Job  {job['job_id'][:14]}…[/bold]",
            "",
            f"Status : [{c}]{job['status'].upper()}[/{c}]",
            f"Domain : [cyan]{job['domain']}[/cyan]",
            f"Files  : {job['processed_count']} / {job['file_count']}",
            f"Queued : {_fmt(job.get('queued_at'))}",
            f"Started: {_fmt(job.get('started_at'))}",
            "",
            "[dim]Navigate to a file node to see chunk details.[/dim]",
        ]
        self.update("\n".join(lines))

    def show_file(self, entry: dict) -> None:
        short = entry["filename"].split("/")[-1]
        c = _STATUS_COLOR.get(entry["status"], "white")
        step = entry.get("current_step") or "—"
        lines = [
            f"[bold]{short}[/bold]",
            "",
            f"Status : [{c}]{entry['status'].upper()}[/{c}]",
            f"Step   : [dim]{step}[/dim]",
            "",
        ]
        sha = entry.get("doc_sha256")
        if sha:
            chunks = _fetch_chunks(sha)
            if chunks:
                total = len(chunks)
                e_done = sum(1 for ch in chunks if ch["e"] in ("ok", "skipped"))
                p_done = sum(1 for ch in chunks if ch["p"] in ("ok", "skipped"))
                r_done = sum(1 for ch in chunks if ch["r"] in ("ok", "skipped"))
                lines += [
                    f"[bold]Chunks  ({total} total)[/bold]",
                    _progress_bar("Entities     ", e_done, total),
                    _progress_bar("Properties   ", p_done, total),
                    _progress_bar("Relationships", r_done, total),
                    "",
                    "[bold dim]  seq   E  P  R[/bold dim]",
                    "[dim]  ───  ── ── ──[/dim]",
                ]
                for ch in chunks:
                    lines.append(
                        f"  {ch['seq']:>3}    {_icon(ch['e'])}  {_icon(ch['p'])}  {_icon(ch['r'])}"
                    )
            else:
                lines.append("[dim]No chunk data yet.[/dim]")
        else:
            lines.append("[dim]Chunk data not yet available.[/dim]")
        self.update("\n".join(lines))


# ── App ───────────────────────────────────────────────────────────────────────


class DashboardApp(App):
    TITLE = "Artmind — Ingestion Dashboard"

    CSS = """
    Screen { layers: base; }

    TabbedContent, ContentSwitcher { height: 1fr; }

    #active-split { height: 1fr; }

    Tree {
        width: 1fr;
        border: solid $primary;
        padding: 0 1;
    }

    #detail-scroll {
        width: 1fr;
        border: solid $success;
    }

    DataTable { height: 1fr; }

    #log-view {
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh now"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Active Jobs", id="tab-active"):
                with Horizontal(id="active-split"):
                    yield Tree("📦 Active / Queued", id="jobs-tree")
                    with VerticalScroll(id="detail-scroll"):
                        yield DetailPanel(id="detail-panel")
            with TabPane("Completed Jobs", id="tab-completed"):
                yield DataTable(id="completed-table", cursor_type="row")
            with TabPane("Worker Log", id="tab-log"):
                yield RichLog(id="log-view", highlight=False, markup=False, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        tbl = self.query_one("#completed-table", DataTable)
        tbl.add_columns(
            "Job ID", "Status", "Domain",
            "File", "File Status", "Started", "Completed", "Error",
        )
        self._rebuild_tree()
        self._rebuild_completed_table()
        self._refresh_log()
        self.set_interval(2, self._refresh_log)
        self.sub_title = "Detail updates on navigation · R to rebuild tree"

    # ── refresh ───────────────────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#jobs-tree", Tree)
        tree.clear()
        jobs = _fetch_active_jobs()

        if not jobs:
            tree.root.add_leaf("[dim]No active or queued jobs[/dim]")
            self.query_one(DetailPanel).show_placeholder()
            return

        first_processing = None

        for job in jobs:
            c = _STATUS_COLOR.get(job["status"], "white")
            i = _STATUS_ICON.get(job["status"], "?")
            job_label = (
                f"[{c}]{i}[/{c}] {job['job_id'][:10]}…  "
                f"[{c}]{job['status'].upper()}[/{c}]  "
                f"[dim]{job['processed_count']}/{job['file_count']} files · "
                f"{job['domain']} · {_fmt(job.get('started_at') or job.get('queued_at'))}[/dim]"
            )
            job_node = tree.root.add(job_label, data={"type": "job", "job": job})
            job_node.expand()

            for f in job["files"]:
                fc = _STATUS_COLOR.get(f["status"], "white")
                fi = _STATUS_ICON.get(f["status"], "?")
                step_str = f" [dim]→ {f['current_step']}[/dim]" if f.get("current_step") else ""
                file_label = (
                    f"  [{fc}]{fi}[/{fc}] {f['filename'].split('/')[-1]}{step_str}"
                )
                leaf = job_node.add_leaf(file_label, data={"type": "file", "file": f, "job_id": job["job_id"]})
                if first_processing is None and f["status"] == "processing":
                    first_processing = leaf

        # Auto-show the hottest active file in the detail panel on each refresh
        if first_processing is not None:
            self.query_one(DetailPanel).show_file(first_processing.data["file"])

    def _rebuild_completed_table(self) -> None:
        tbl = self.query_one("#completed-table", DataTable)
        tbl.clear()
        for job in _fetch_completed_jobs():
            jc = _STATUS_COLOR.get(job["status"], "white")
            job_status_text = Text(job["status"].upper(), style=f"bold {jc}")
            job_id_short = job["job_id"][:14] + "…"

            if not job["files"]:
                tbl.add_row(
                    job_id_short, job_status_text, job["domain"],
                    "[dim]—[/dim]", "[dim]—[/dim]",
                    _fmt(job.get("started_at")), _fmt(job.get("completed_at")), "",
                )
                continue

            for idx, f in enumerate(job["files"]):
                fc = _STATUS_COLOR.get(f["status"], "white")
                file_status_text = Text(f["status"].upper(), style=fc)
                short_name = f["filename"].split("/")[-1]
                err = (f.get("error_message") or "")[:40]
                tbl.add_row(
                    job_id_short if idx == 0 else "",
                    job_status_text if idx == 0 else Text(""),
                    job["domain"] if idx == 0 else "",
                    short_name,
                    file_status_text,
                    _fmt(f.get("started_at")),
                    _fmt(f.get("completed_at")),
                    err,
                )

    # ── tree events ───────────────────────────────────────────────────────────

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Re-fetch from DB every time the cursor moves to a node."""
        data = event.node.data
        if not data:
            return
        panel = self.query_one(DetailPanel)
        if data["type"] == "job":
            fresh = _fetch_job(data["job"]["job_id"])
            panel.show_job(fresh if fresh else data["job"])
        else:
            file_entry = data["file"]
            fresh = _fetch_file_entry(data["job_id"], file_entry["filename"])
            panel.show_file(fresh if fresh else file_entry)

    def _refresh_log(self) -> None:
        log_view = self.query_one("#log-view", RichLog)
        lines = _read_log_tail(100)
        log_view.clear()
        for line in lines:
            log_view.write(Text(line, style=_log_line_style(line)))

    def action_refresh(self) -> None:
        """Rebuild tree from DB and snap detail panel to the first job."""
        self._rebuild_tree()
        self._rebuild_completed_table()
        self.sub_title = f"Rebuilt {datetime.now().strftime('%H:%M:%S')} · Detail updates on navigation"
        # Move cursor and detail panel to the first job node
        tree = self.query_one("#jobs-tree", Tree)
        children = list(tree.root.children)
        if children:
            first_job_node = children[0]
            tree.move_cursor(first_job_node)
            data = first_job_node.data
            if data and data["type"] == "job":
                fresh = _fetch_job(data["job"]["job_id"])
                self.query_one(DetailPanel).show_job(fresh if fresh else data["job"])


def run_dashboard() -> None:
    DashboardApp().run()
