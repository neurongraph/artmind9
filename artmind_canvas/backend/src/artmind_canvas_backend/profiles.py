"""The canvas agent profile.

Reuses artmind's ``AgentProfile`` (skill scoping + persona) but gives the canvas
its own persona. Skills mirror the admin set for now — a superset suited to the
canvas's read/write/maintain surface — so every referenced skill is one the run
folder actually seeds. A canvas-specific skill set can diverge later.
"""

from dataclasses import replace

from artmind.webui.profiles import ADMIN_PROFILE

CANVAS_SYSTEM_APPEND = """\
You are the artmind_canvas assistant. The user works in a spatial Canvas of
Cards alongside this chat. artmind is your brain: route questions and writes
through the artmind skills (artmind-query, artmind-update, artmind-refine,
artmind-create-schema, artmind-ingestion-helper). Answer conversationally. This
is not a coding session: do not explore or explain the artmind source code and
never use graphify."""

CANVAS_PROFILE = replace(
    ADMIN_PROFILE,
    name="canvas",
    system_append=CANVAS_SYSTEM_APPEND,
)
