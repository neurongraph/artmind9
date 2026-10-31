"""Shared helpers for the slow tests that run the real docling (marker
`slow_docling`, `just dev-test-docling`).

docling is run from the interpreter's own environment rather than through
`uv run`: a checkout or worktree without its own venv would otherwise build a
second one with the heavy ingest extra.
"""

import sys
from pathlib import Path

DOCS = Path(__file__).parent / "data" / "docs"
GOLDEN = Path(__file__).parent / "data" / "golden"

#: The pptx fixtures whose converted bodies are pinned (`GOLDEN/pptx_bodies`,
#: recorded from the conversion on docling 2.145 before `docling_render`).
PPTX_FIXTURES = (
    "project_governance/ProjectMeridian_ExecutiveGovernance_Q1-2025.pptx",
    "project_governance/Sample_DataFunctions_Monthly_Governance.pptx",
    "sales_collateral/Cloud1.pptx",
    "sales_collateral/Data1.pptx",
    "sales_collateral/Digital1.pptx",
    "sales_collateral/Payment1.pptx",
    "general/Stock market investments.pptx",
)

#: What every image is described as, so a body is deterministic and nothing
#: leaves the machine.
DESCRIBED = "[picture]"


def golden_path(rel: str) -> Path:
    return GOLDEN / "pptx_bodies" / (Path(rel).stem + ".md")


def convert_real(monkeypatch, tmp_path: Path, rel: str) -> tuple[str, dict]:
    """The body and conversion record `ingest._convert_binary_via_docling`
    gives for the fixture `rel`, with the real docling, in a throwaway
    markdowns dir."""
    from artmind import ingest as ing

    docling = str(Path(sys.executable).parent / "docling")
    real_run = ing.run_command

    def run(cmd, *args, **kwargs):
        if isinstance(cmd, str) and cmd.startswith("uv run docling"):
            cmd = docling + cmd[len("uv run docling"):]
        return real_run(cmd, *args, **kwargs)

    markdowns = tmp_path / "markdowns"
    markdowns.mkdir()
    monkeypatch.setattr(ing, "run_command", run)
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", markdowns)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(ing, "_describe_image", lambda image, model: DESCRIBED)
    body, info = ing._convert_binary_via_docling((DOCS / rel).resolve(), "unused-model")
    assert body is not None, info
    return body, info["conversion"]
