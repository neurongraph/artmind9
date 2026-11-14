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
  maintenance set (curate, update/retract, schema authoring, and ingestion
  guidance). Routine bulk ingestion + job monitoring run through the
  deterministic dashboard widgets (Lane B); the agent carries
  ``artmind-ingestion-helper`` for guidance and troubleshooting.

Each front-end app binds a profile when it constructs its ``SessionRegistry``
(see ``backends.backend_factory``); both profiles run through both backends.

This module is deliberately dependency-free (no Claude SDK, no FastAPI) so any
backend can import it without dragging in unrelated machinery.
"""

import dataclasses
import os
import re
from dataclasses import dataclass

QA_SYSTEM_APPEND = """\
You are the artmind assistant, an end-user interface to the artmind knowledge
system. Users ask about knowledge stored in artmind domains, and may contribute
or correct facts. Route their requests through the artmind skills: artmind-query
for questions (it checks for a saved view first, via `artmind query views`),
artmind-update for adding or correcting facts. Graph maintenance,
schema authoring, and document ingestion are handled by the admin console, not
here — if a user asks for those, tell them to use the admin UI. This is not a
coding session: do not explore or explain the artmind source code and never use
graphify. Answer conversationally; no raw JSON or command output unless asked.
The UI adds query provenance automatically; do not add a Sources section yourself."""

ADMIN_SYSTEM_APPEND = """\
You are the artmind admin assistant, an operator interface for maintaining the
artmind knowledge graph. Operators ask you to curate, update, retract, and
reconcile knowledge, inspect timelines and conflicts, author domain schemas,
and assist with document ingestion. Route requests through the artmind skills:
artmind-curate for graph maintenance (same-as review, conflict adjudication,
synthesis), artmind-update for adding, retracting, and correcting facts,
artmind-create-schema for new domains and schemas, artmind-create-view for
saved graph views (reusable parameterised queries), artmind-build-topic for topic
plans on broad questions (dry run first; build only after the user's explicit yes),
artmind-ingestion-helper for
guiding and troubleshooting ingestion, and artmind-query for inspecting the
graph. Routine bulk ingestion and job monitoring run through the dashboard
widgets — reach for artmind-ingestion-helper when an operator needs guidance
or troubleshooting.
This is not a coding session: do not explore or explain the artmind source code
and never use graphify. Explain what a maintenance operation will do before you
run anything destructive. The UI adds query provenance automatically; do not
add a Sources section yourself."""

BENCHMARK_SYSTEM_APPEND = """\
You are answering ONE question from an unattended batch benchmark run. There is
no user on the other end of this conversation and no memory of any other
question in the batch — treat this as a cold, standalone question. Follow the
artmind-query skill's protocol exactly. Critical difference from an interactive
chat: you must NEVER ask a clarifying question back, because nobody will
answer it. If the domain or an entity reference is ambiguous, resolve it
yourself using the skill's own orientation/resolution steps (orient,
entity-resolve) and state the ambiguity and your choice in the answer instead
of stopping. Always state which domain(s) and documents grounded the answer.
Do not use artmind-update — this is a read-only benchmark, never write to the
graph. This is not a coding session: do not explore or explain the artmind
source code and never use graphify."""


@dataclass(frozen=True)
class AgentProfile:
    """The persona + skill scoping for one agent-driven web-UI surface."""

    name: str
    skills: tuple[str, ...]
    system_append: str
    acp_mode: str | None = None
    # Whether this surface may read the vault directly. False means answers
    # must come through `artmind query`, so they carry supersession, conflicts
    # and provenance (docs/vault.md). True is for OPERATOR surfaces, where
    # inspecting a failed conversion or a log is the job.
    filesystem_access: bool = False
    # The skill variant answering questions: classic (artmind-query), or X for
    # the artmind-query-X skill. See with_variant.
    contract: str = "classic"


QA_PROFILE = AgentProfile(
    name="qa",
    skills=(
        "artmind-query",
        "artmind-update",
        "artmind-present",
    ),
    system_append=QA_SYSTEM_APPEND,
    acp_mode="artmind",
    # End-user Q&A: reading files is pure downside.
    filesystem_access=False,
)

ADMIN_PROFILE = AgentProfile(
    name="admin",
    skills=(
        "artmind-query",
        "artmind-update",
        "artmind-present",
        "artmind-curate",
        "artmind-create-schema",
        "artmind-create-view",
        "artmind-build-topic",
        "artmind-ingestion-helper",
    ),
    system_append=ADMIN_SYSTEM_APPEND,
    acp_mode="artmind-admin",
    # Operator console: inspecting a failed conversion or reading a log is the job.
    filesystem_access=True,
)

BENCHMARK_PROFILE = AgentProfile(
    name="benchmark",
    skills=("artmind-query",),
    system_append=BENCHMARK_SYSTEM_APPEND,
    acp_mode="artmind",
    # A benchmark that greps its way to an answer silently invalidates every score.
    filesystem_access=False,
)

PROFILES: dict[str, AgentProfile] = {
    p.name: p for p in (QA_PROFILE, ADMIN_PROFILE, BENCHMARK_PROFILE)
}


CONTRACT_ENV = "ARTMIND_AGENT_CONTRACT"
CLASSIC = "classic"
_VARIANT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def skill_for(variant: str) -> str:
    """The query skill folder of a skill variant: classic is artmind-query, X is artmind-query-X."""
    if not isinstance(variant, str) or not _VARIANT_RE.fullmatch(variant):
        raise ValueError(f"a skill variant is lowercase letters, digits and dashes (max 40), got {variant!r}")
    return "artmind-query" if variant == CLASSIC else f"artmind-query-{variant}"


def variant_from_env() -> str:
    raw = (os.environ.get(CONTRACT_ENV) or "").strip()
    if not raw:
        return CLASSIC
    skill_for(raw)  # validates
    return raw


def with_variant(profile: AgentProfile, variant: str) -> AgentProfile:
    """`profile` answering with the query skill of `variant` (spec 2026-11-08 section 2.2)."""
    new = skill_for(variant)
    if variant == profile.contract:
        return profile
    old = skill_for(profile.contract)
    skills = tuple(new if s == old else s for s in profile.skills)
    # Every mention of the old skill name in the prompt (not a longer skill name sharing its prefix).
    append = re.sub(rf"{re.escape(old)}(?![\w-])", new, profile.system_append)
    return dataclasses.replace(profile, skills=skills, system_append=append, contract=variant)


def require_skill(profile: AgentProfile) -> None:
    """Raise ValueError when a non-classic variant's skill is not installed where agents look for skills."""
    if profile.contract == CLASSIC:
        return
    import paths

    name = skill_for(profile.contract)
    if not (paths.ARTMIND_AGENT_CWD / ".claude" / "skills" / name / "SKILL.md").exists():
        raise ValueError(f"skill variant {profile.contract!r} needs the {name} skill in this vault's .claude/skills")


# Kept names for the cherry-picked callers in app.py and benchmark_routes.py.
contract_from_env = variant_from_env
with_contract = with_variant
