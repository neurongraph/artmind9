"""Agent profiles: skill set + persona per web-UI surface.

A *profile* bundles everything that distinguishes one agent-driven surface from
another: which artmind skills the agent may use, the system-prompt text
appended for each turn, and the opencode ACP *mode* that carries the same
persona to an ACP agent. Everything else — the backend transport, the session
registry, the SSE event contract — is profile-agnostic.

The two surfaces:

- ``QA_PROFILE`` — the end-user chat UI: ask questions and contribute/correct
  facts (query + update only). Graph maintenance, schema authoring, and
  ingestion are deliberately *not* here — those are operator concerns. (The old
  hardcoded persona lumped them together when one agent served everyone; the
  split is the point of profiles.)
- ``ADMIN_PROFILE`` — the operator console (Lane A of the admin UI): the full
  maintenance set (refine, update/supersede, schema authoring, and ingestion
  guidance). Routine bulk ingestion + job monitoring run through the
  deterministic dashboard widgets (Lane B); the agent carries
  ``artmind-ingestion-helper`` for guidance and troubleshooting.

Each front-end app binds a profile when it constructs its ``SessionRegistry``
(see ``backends.backend_factory``); both profiles run through both backends.

This module is deliberately dependency-free (no Claude SDK, no FastAPI) so any
backend can import it without dragging in unrelated machinery.
"""

from dataclasses import dataclass

QA_SYSTEM_APPEND = """\
You are the artmind assistant, an end-user interface to the artmind knowledge
system. Users ask about knowledge stored in artmind domains, and may contribute
or correct facts. Route their requests through the artmind skills: artmind-query
for questions, artmind-update for adding or correcting facts. Graph maintenance,
schema authoring, and document ingestion are handled by the admin console, not
here — if a user asks for those, tell them to use the admin UI. This is not a
coding session: do not explore or explain the artmind source code and never use
graphify. Answer conversationally; no raw JSON or command output unless asked."""

ADMIN_SYSTEM_APPEND = """\
You are the artmind admin assistant, an operator interface for maintaining the
artmind knowledge graph. Operators ask you to refine, update, supersede, and
reconcile knowledge, inspect timelines and conflicts, author domain schemas,
and assist with document ingestion. Route requests through the artmind skills:
artmind-refine for graph maintenance (merging, consolidation), artmind-update
for adding, superseding, and correcting facts, artmind-create-schema for new
domains and schemas, artmind-ingestion-helper for guiding and troubleshooting
ingestion, and artmind-query for inspecting the graph. Routine bulk ingestion
and job monitoring run through the dashboard widgets — reach for
artmind-ingestion-helper when an operator needs guidance or troubleshooting.
This is not a coding session: do not explore or explain the artmind source code
and never use graphify. Explain what a maintenance operation will do before you
run anything destructive."""


@dataclass(frozen=True)
class AgentProfile:
    """The persona + skill scoping for one agent-driven web-UI surface."""

    name: str
    skills: tuple[str, ...]
    system_append: str
    acp_mode: str | None = None


QA_PROFILE = AgentProfile(
    name="qa",
    skills=(
        "artmind-query",
        "artmind-update",
    ),
    system_append=QA_SYSTEM_APPEND,
    acp_mode="artmind",
)

ADMIN_PROFILE = AgentProfile(
    name="admin",
    skills=(
        "artmind-query",
        "artmind-update",
        "artmind-refine",
        "artmind-create-schema",
        "artmind-ingestion-helper",
    ),
    system_append=ADMIN_SYSTEM_APPEND,
    acp_mode="artmind-admin",
)

PROFILES: dict[str, AgentProfile] = {p.name: p for p in (QA_PROFILE, ADMIN_PROFILE)}
