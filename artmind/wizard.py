import json
import subprocess
from pathlib import Path

import jq as _jq
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabPane,
    TabbedContent,
    Tree,
)
from textual import work


def apply_jq_filter(raw_output: str, expression: str) -> str:
    """Apply a jq expression to raw JSON output. Returns formatted result or error."""
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, ValueError):
        return "error: output is not valid JSON"
    try:
        compiled = _jq.compile(expression)
        results = compiled.input(data).all()
        if len(results) == 1:
            return json.dumps(results[0], indent=2)
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"error: {e}"


class CommandForm(VerticalScroll):
    """Dynamically generated form for a command's arguments."""

    DEFAULT_CSS = """
    CommandForm {
        height: auto;
        max-height: 14;
        border: solid $primary-darken-1;
        padding: 0 1;
        margin-bottom: 1;
    }
    CommandForm Label {
        color: $text-muted;
        height: 1;
    }
    CommandForm Input {
        height: 3;
        margin-bottom: 0;
    }
    CommandForm Select {
        height: 3;
        margin-bottom: 0;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._global_field_counter = 0
        self._field_widgets: dict[str, Widget] = {}  # Map flag to widget

    def build_form(
        self,
        cmd_id: str,
        data_source: str,
        session_id: str | None = None,
    ) -> None:
        from artmind.wizard_commands import COMMANDS
        # Clear all children and reset field tracking
        for child in list(self.children):
            child.remove()
        self._field_widgets.clear()

        if cmd_id not in COMMANDS:
            return
        cmd = COMMANDS[cmd_id]
        for arg in cmd["args"]:
            effective_arg = dict(arg)
            # Pre-populate session_id for update.confirm
            if cmd_id == "update.confirm" and arg["flag"] == "--session" and session_id:
                effective_arg["sample_value"] = session_id
            self._add_field(effective_arg, data_source)

    def _add_field(self, arg: dict, data_source: str) -> None:
        from paths import WIZARD_FIXTURES_DIR

        flag = arg["flag"]
        label_text = ("* " if arg["required"] else "") + arg["label"]
        # Make widget IDs unique by including a global counter
        widget_id = f"arg_{self._global_field_counter}_{flag.lstrip('-').replace('-', '_')}"
        self._global_field_counter += 1

        value = ""
        if data_source == "sample" and arg["sample_value"]:
            sv = arg["sample_value"]
            value = str(WIZARD_FIXTURES_DIR / "sample_fiction.md") if sv == "__FIXTURE__" else sv

        self.mount(Label(label_text))

        widget = None
        if arg["type"] == "select" and flag == "--domain":
            domains = self._fetch_domains()
            options = [(d, d) for d in domains]
            selected = value if value in domains else (domains[0] if domains else "fiction")
            widget = Select(options, value=selected, id=widget_id)
            self.mount(widget)
        elif arg["type"] == "bool":
            widget = Input(
                value="",
                placeholder="type 'true' to enable (leave blank to omit)",
                id=widget_id,
            )
            self.mount(widget)
        else:
            widget = Input(value=value, placeholder=arg["placeholder"], id=widget_id)
            self.mount(widget)

        # Store widget reference for later retrieval
        if widget:
            self._field_widgets[flag] = widget

    @staticmethod
    def _fetch_domains() -> list[str]:
        try:
            result = subprocess.run(
                ["artmind", "domains", "list"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return lines or ["fiction"]
        except Exception:
            return ["fiction"]


class WizardApp(App):
    TITLE = "artmind wizard"

    CSS = """
    Screen { layout: vertical; }

    #main { height: 1fr; layout: horizontal; }

    #lifecycle-tree {
        width: 30;
        border-right: solid $primary-darken-1;
    }

    #command-panel {
        width: 1fr;
        padding: 1 2;
    }

    #teaching-text {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    #cli-preview {
        height: auto;
        background: $surface;
        color: $accent;
        padding: 0 1;
        margin-bottom: 1;
        border: solid $primary-darken-2;
    }

    #output-tabs { height: 1fr; }

    #action-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
        border-top: solid $primary-darken-1;
    }

    .locked { color: $text-disabled; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f1", "show_help", "Help"),
        Binding("enter", "run_command", "Run"),
        Binding("g", "toggle_guided", "Guided"),
        Binding("f", "toggle_free", "Free"),
        Binding("d", "toggle_sample", "Sample"),
        Binding("r", "toggle_real", "Real"),
    ]

    mode: reactive[str] = reactive("guided")
    data_source: reactive[str] = reactive("sample")
    completed_stages: reactive[frozenset] = reactive(frozenset())
    last_session_id: reactive[str | None] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self._current_cmd_id: str | None = None
        self._last_raw_output: str = ""
        self._last_ingested_doc_stem: str | None = None
        self._last_domain: str | None = None
        self._locked_stage_indices: set[int] = set()
        self._view_pane_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield Tree("LIFECYCLE", id="lifecycle-tree")
            with Vertical(id="command-panel"):
                yield Static("Select a command from the tree.", id="teaching-text")
                yield CommandForm(id="command-form")
                yield Static("", id="cli-preview")
                yield Static("", id="action-bar")
                with TabbedContent(id="output-tabs"):
                    with TabPane("Raw", id="tab-raw"):
                        yield RichLog(highlight=True, markup=True, id="output-log")
                    with TabPane("Custom jq", id="tab-custom-jq"):
                        yield Input(placeholder=".entities | length", id="jq-input")
                        yield RichLog(highlight=True, markup=True, id="jq-output-log")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_tree()
        self.query_one("#action-bar", Static).update(
            "[cyan]MODES:[/cyan] [yellow]G[/yellow]=Guided [yellow]F[/yellow]=Free | [cyan]DATA:[/cyan] [yellow]D[/yellow]=Sample [yellow]R[/yellow]=Real | "
            "[cyan]RUN:[/cyan] [bold]ENTER[/bold] | [cyan]HELP:[/cyan] [yellow]F1[/yellow] | [cyan]QUIT:[/cyan] [yellow]Q[/yellow]"
        )
        # Show a help notification
        self.notify(
            "Keyboard shortcuts: G/F=toggle mode, D/R=toggle data, ENTER=run command, Q=quit, F1=help. "
            "Select a command from the tree on the left.",
            title="Welcome to artmind wizard!",
            timeout=6
        )

    # ── Tree population ──────────────────────────────────────────────────────

    def _populate_tree(self) -> None:
        from artmind.wizard_commands import COMMANDS, INFO_NODES, STAGES

        tree = self.query_one("#lifecycle-tree", Tree)
        tree.root.expand()
        for stage in STAGES:
            stage_num = stage["num"]
            stage_node = tree.root.add(stage["label"], expand=True)
            for cmd_id, cmd in COMMANDS.items():
                if cmd["stage"] == stage_num:
                    stage_node.add_leaf(cmd["label"], data=cmd_id)
            for info_id, info in INFO_NODES.items():
                if info["stage"] == stage_num:
                    stage_node.add_leaf(f"ℹ  {info['label']}", data=f"info:{info_id}")

        if self.mode == "guided":
            self._apply_guided_locking()

    def _apply_guided_locking(self) -> None:
        tree = self.query_one("#lifecycle-tree", Tree)
        next_unlocked = max(self.completed_stages, default=0) + 1
        for i, stage_node in enumerate(tree.root.children, start=1):
            should_lock = i > next_unlocked
            if should_lock and i not in self._locked_stage_indices:
                stage_node.set_label(f"[dim]{stage_node.label.plain}[/dim]")
                self._locked_stage_indices.add(i)
            elif not should_lock and i in self._locked_stage_indices:
                stage_node.set_label(stage_node.label.plain)
                self._locked_stage_indices.discard(i)

    def _apply_mode_to_tree(self) -> None:
        if self.mode == "free":
            tree = self.query_one("#lifecycle-tree", Tree)
            for i, node in enumerate(tree.root.children, start=1):
                if i in self._locked_stage_indices:
                    node.set_label(node.label.plain)
                    self._locked_stage_indices.discard(i)
        else:
            self._apply_guided_locking()

    # ── Tree selection ───────────────────────────────────────────────────────

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        if node.data is None:
            return
        cmd_id: str = node.data
        if cmd_id.startswith("info:"):
            self._show_info_node(cmd_id[5:])
            return
        from artmind.wizard_commands import COMMANDS
        if cmd_id not in COMMANDS:
            return
        cmd = COMMANDS[cmd_id]
        self._current_cmd_id = cmd_id
        self.query_one("#teaching-text", Static).update(cmd["description"])
        self.query_one("#cli-preview", Static).update(f"$ {' '.join(cmd['cli_cmd'])} ...")
        self.query_one("#action-bar", Static).update(
            f"[cyan]Ready to run![/cyan] Press [bold green]ENTER[/bold green] to execute • [bold]Q[/bold]=quit"
        )
        self.query_one("#output-log", RichLog).clear()
        self.query_one(CommandForm).build_form(
            cmd_id, self.data_source, session_id=self.last_session_id
        )

    def _show_info_node(self, info_id: str) -> None:
        from artmind.wizard_commands import INFO_NODES
        info = INFO_NODES.get(info_id, {})
        self.query_one("#teaching-text", Static).update(info.get("description", ""))
        self.query_one("#cli-preview", Static).update("")
        self.query_one("#action-bar", Static).update("")
        log = self.query_one("#output-log", RichLog)
        log.clear()
        if info_id == "ingest.inspect-files":
            self._show_kg_files(log)
        elif "git_commands" in info:
            domain = self._last_domain or "{domain}"
            stem = self._last_ingested_doc_stem or "{doc_stem}"
            for git_cmd in info["git_commands"]:
                log.write(git_cmd.format(domain=domain, doc_stem=stem))
        elif "external_command" in info:
            log.write("[bold]Run in a separate terminal:[/bold]")
            log.write(info["external_command"])

    # ── KG file inspection ───────────────────────────────────────────────────

    @staticmethod
    def _find_kg_dir(
        domain: str,
        doc_stem: str | None,
        kg_base: Path | None = None,
    ) -> Path | None:
        from paths import KG_DIR
        base = kg_base or KG_DIR
        if not doc_stem:
            return None
        candidate = base / domain / doc_stem
        if candidate.exists() and (candidate / "entities.json").exists():
            return candidate
        return None

    def _show_kg_files(self, log: RichLog) -> None:
        from artmind.wizard_commands import INFO_NODES
        info = INFO_NODES["ingest.inspect-files"]
        domain = self._last_domain or "fiction"
        kg_dir = self._find_kg_dir(domain, self._last_ingested_doc_stem)
        if not kg_dir:
            log.write(
                f"[yellow]No extracted KG found for domain '{domain}'. "
                "Run 'extract-kg' or 'sync' first.[/yellow]"
            )
            return
        log.write(f"[bold]KG files in {kg_dir}/[/bold]\n")
        for filename in info["files_to_show"]:
            filepath = kg_dir / filename
            if not filepath.exists():
                log.write(f"[dim]{filename} — not found[/dim]")
                continue
            try:
                data = json.loads(filepath.read_text())
                count = len(data) if isinstance(data, list) else "object"
                log.write(f"\n[bold cyan]{filename}[/bold cyan] ({count} items)")
                preview = data[:3] if isinstance(data, list) else data
                log.write(json.dumps(preview, indent=2))
                if isinstance(data, list) and len(data) > 3:
                    log.write(f"  … and {len(data) - 3} more")
            except Exception as e:
                log.write(f"[red]{filename} — error reading: {e}[/red]")

    # ── Form value helpers ───────────────────────────────────────────────────

    def _build_cli_args(self, cmd_id: str) -> list[str]:
        from artmind.wizard_commands import COMMANDS
        if cmd_id not in COMMANDS:
            return []
        cmd = COMMANDS[cmd_id]
        args = list(cmd["cli_cmd"])
        form = self.query_one(CommandForm)
        for arg in cmd["args"]:
            flag = arg["flag"]
            # Get widget from stored references
            widget = form._field_widgets.get(flag)
            if not widget:
                continue
            if hasattr(widget, "value") and widget.value is Select.BLANK:
                continue
            value = str(widget.value).strip() if hasattr(widget, "value") else ""
            if not value:
                continue
            if arg["type"] == "bool":
                if value.lower() in ("true", "1", "yes"):
                    args.append(flag)
            elif flag.startswith("--"):
                args.extend([flag, value])
            else:
                args.append(value)
        return args

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("arg_") and self._current_cmd_id:
            args = self._build_cli_args(self._current_cmd_id)
            self.query_one("#cli-preview", Static).update("$ " + " ".join(args))

    # ── Command execution ────────────────────────────────────────────────────

    def action_run_command(self) -> None:
        if not self._current_cmd_id:
            return
        args = self._build_cli_args(self._current_cmd_id)
        if not args:
            return
        log = self.query_one("#output-log", RichLog)
        log.clear()
        log.write(f"[dim]$ {' '.join(args)}[/dim]")
        self.query_one("#action-bar", Static).update("Running…")
        self._execute_command(args)

    @work(thread=True)
    def _execute_command(self, args: list[str]) -> None:
        result = subprocess.run(args, capture_output=True, text=True)
        self.call_from_thread(self._handle_result, result)

    def _handle_result(self, result: "subprocess.CompletedProcess[str]") -> None:
        log = self.query_one("#output-log", RichLog)
        self._last_raw_output = result.stdout
        try:
            parsed = json.loads(result.stdout)
            log.write(json.dumps(parsed, indent=2))
        except (json.JSONDecodeError, ValueError):
            if result.stdout:
                log.write(result.stdout)
        if result.stderr:
            log.write(f"[red]{result.stderr}[/red]")
        if result.returncode == 0:
            self.query_one("#action-bar", Static).update("[green]✓ Exit 0[/green]")
            self._on_command_success()
        else:
            self.query_one("#action-bar", Static).update(
                f"[red]✗ Exit {result.returncode}[/red]"
            )
        self._rebuild_view_tabs(self._current_cmd_id or "")

    def _on_command_success(self) -> None:
        if not self._current_cmd_id:
            return
        from artmind.wizard_commands import COMMANDS
        if self._current_cmd_id in COMMANDS:
            self._complete_stage(COMMANDS[self._current_cmd_id]["stage"])
        if self._current_cmd_id == "update.draft":
            self._extract_and_store_session_id(self._last_raw_output)
        if self._current_cmd_id in ("ingest.sync", "ingest.extract-kg"):
            self._store_last_doc_from_output(self._last_raw_output)

    # ── Guided mode stage progression ────────────────────────────────────────

    def _complete_stage(self, stage_num: int) -> None:
        self.completed_stages = frozenset(self.completed_stages | {stage_num})
        tree = self.query_one("#lifecycle-tree", Tree)
        stage_node = list(tree.root.children)[stage_num - 1]
        plain = stage_node.label.plain
        if "✓" not in plain:
            stage_node.set_label(plain + " ✓")
        if self.mode == "guided":
            self._apply_guided_locking()

    def _extract_and_store_session_id(self, raw_output: str) -> None:
        try:
            data = json.loads(raw_output)
            if "session_id" in data:
                self.last_session_id = data["session_id"]
        except (json.JSONDecodeError, ValueError):
            pass

    def _store_last_doc_from_output(self, raw_output: str) -> None:
        try:
            data = json.loads(raw_output)
            if "document_name" in data:
                self._last_ingested_doc_stem = Path(data["document_name"]).stem
            if "domain" in data:
                self._last_domain = data["domain"]
        except (json.JSONDecodeError, ValueError):
            pass

    # ── jq output views ──────────────────────────────────────────────────────

    def _rebuild_view_tabs(self, cmd_id: str) -> None:
        from artmind.wizard_commands import COMMANDS
        tabs = self.query_one("#output-tabs", TabbedContent)
        # Remove all view panes (keep only raw and custom jq tabs)
        for pane in list(tabs.query(TabPane)):
            if pane.id not in ("tab-raw", "tab-custom-jq"):
                pane.remove()
        self._view_pane_ids.clear()

        if cmd_id not in COMMANDS:
            return
        for view_name, expr in COMMANDS[cmd_id].get("views", {}).items():
            tab_id = "tab-view-" + view_name.lower().replace(" ", "-")
            filtered = apply_jq_filter(self._last_raw_output, expr)
            pane = TabPane(view_name, Static(filtered), id=tab_id)
            tabs.add_pane(pane)
            self._view_pane_ids.add(tab_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "jq-input":
            filtered = apply_jq_filter(self._last_raw_output, event.value)
            jq_log = self.query_one("#jq-output-log", RichLog)
            jq_log.clear()
            jq_log.write(filtered)

    # ── Mode / data source toggles ───────────────────────────────────────────

    def action_toggle_guided(self) -> None:
        self.mode = "guided"
        self._apply_mode_to_tree()
        self.notify("Mode: Guided (locked stages)", timeout=2)

    def action_toggle_free(self) -> None:
        self.mode = "free"
        self._apply_mode_to_tree()
        self.notify("Mode: Free (all commands available)", timeout=2)

    def action_toggle_sample(self) -> None:
        self.data_source = "sample"
        if self._current_cmd_id:
            self.query_one(CommandForm).build_form(
                self._current_cmd_id, self.data_source, self.last_session_id
            )
        self.notify("Data source: Sample", timeout=2)

    def action_toggle_real(self) -> None:
        self.data_source = "real"
        if self._current_cmd_id:
            self.query_one(CommandForm).build_form(
                self._current_cmd_id, self.data_source, self.last_session_id
            )
        self.notify("Data source: Real", timeout=2)

    def action_show_help(self) -> None:
        self.notify(
            "artmind wizard — use the tree to navigate lifecycle stages. "
            "Enter runs the selected command. Q quits.",
            title="Help",
        )


def run_wizard() -> None:
    WizardApp().run()
