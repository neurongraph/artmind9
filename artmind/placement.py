"""A6 — Placement classifier (ADR 0012).

Given a block of text (typically a new note or chat-authored fact), propose
``{domain, title, area, project, tags, target_file_hint}`` with confidence.
The proposal is grounded in the **existing controlled vocabulary** (A5) so it
lands on labels already in use rather than inventing new ones.

This module suggests only — it never writes. Consumers (the canvas placement
Card, chat-ui) render the proposal for user review and only then write
frontmatter and trigger re-ingest through the confirmed doc-first path
(ADR 0002 / Q14).

Every proposed field maps onto a real frontmatter field
(docs/document-identity.md's contract) once accepted, and the two halves of
that contract stay distinct here too:

- ``title``, ``area``, ``project``, ``tags`` are **authored** fields — artmind
  seeds them once (title from the filename stem, on first ingest) and never
  overwrites them again, exactly like a human editing them by hand. This
  module's proposals for them are a first draft, not a fill managed
  automatically after the first write.
- ``domain`` proposes the value for **`_domain`** — a system-owned, `_`-prefixed
  field, not a CLI-only argument any more (a document finally declares which
  schema extracts it). It is treated as always-confirmed here regardless: a
  domain change forces re-extraction with a different schema (ADR 0006 (f) /
  Q23c), so this proposal is explicitly a *suggestion* the user must accept,
  never a default a consumer should merge in unattended the way it might for
  the authored fields above.

``target_file_hint`` is a related but separate thing: a kebab-case suggestion
for the file's own name/path, not the human-readable ``title`` that ends up in
its frontmatter — accepting one says nothing about the other.
"""
from __future__ import annotations

import json
import re
from typing import Sequence

import json_repair
from loguru import logger


CONFIDENCE_FLOOR = 0.3


_PROMPT = """You are helping a knowledge-management system decide where a new note belongs.

The system organizes notes along four axes:
- domain: the functional schema that governs how the note's content is extracted
- area: a broad category (e.g. engineering, sales, legal, personal)
- project: a specific initiative or client
- tags: free-form keywords

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONTROLLED VOCABULARY (already in use)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Prefer values from this vocabulary. Only invent a new label when nothing here fits.

Available domains:
{domains}

Existing projects (with document counts):
{projects}

Existing areas (with document counts):
{areas}

Existing tags (with document counts):
{tags}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INPUT TEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{text}

{context_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return only this JSON object. No preamble, no explanation, no markdown fences.

{{
  "domain": string,                         // one of the available domains, or a new one
  "domain_confidence": number between 0 and 1,
  "title": string | null,                   // a human-readable title for this note's frontmatter
  "title_confidence": number between 0 and 1,
  "area": string | null,
  "area_confidence": number between 0 and 1,
  "project": string | null,
  "project_confidence": number between 0 and 1,
  "tags": [string, ...],                    // 0-5 tags; prefer existing values
  "tags_confidence": number between 0 and 1,
  "target_file_hint": string | null,        // optional: suggested filename stem (kebab-case)
  "reason": string                          // one sentence explaining the placement
}}
"""


def _format_vocab_facet(entries: list[dict], limit: int = 30) -> str:
    """Render a vocabulary facet as ``- value (count)`` lines, capped to ``limit``.

    Rare labels below the cap are dropped from the prompt so the LLM's context
    stays focused on the labels the user actually reuses. The count grounds the
    LLM's preference — a project with 20 documents is a stronger signal than
    one with a single note.
    """
    if not entries:
        return "  (none yet)"
    lines = []
    for entry in entries[:limit]:
        value = entry.get("value")
        count = entry.get("count", 0)
        if value:
            lines.append(f"  - {value} ({count})")
    return "\n".join(lines) if lines else "  (none yet)"


def _parse_json_response(text: str) -> dict:
    """Best-effort JSON parse of the LLM output.

    Mirrors artmind.extraction.parse_json_response: strips <think> reasoning
    blocks (some local models emit them) and markdown fences before handing
    off to json_repair, which is tolerant of trailing commas / unquoted keys.
    """
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    parsed = json_repair.loads(text.strip())
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return {}


def build_prompt(
    text: str,
    vocabulary: dict,
    available_domains: Sequence[str],
    context: str | None = None,
) -> str:
    """Render the classifier prompt from text + vocabulary + optional context."""
    context_block = ""
    if context:
        context_block = (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "ADDITIONAL CONTEXT\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{context}\n"
        )
    domain_lines = "\n".join(f"  - {d}" for d in available_domains) if available_domains else "  - general"
    return _PROMPT.format(
        domains=domain_lines,
        projects=_format_vocab_facet(vocabulary.get("project", [])),
        areas=_format_vocab_facet(vocabulary.get("area", [])),
        tags=_format_vocab_facet(vocabulary.get("tags", []), limit=50),
        text=text.strip(),
        context_block=context_block,
    )


def _normalize_proposal(raw: dict, vocabulary: dict, available_domains: Sequence[str]) -> dict:
    """Clamp and coerce the LLM's raw output into a stable proposal shape.

    - Coerces missing/None fields to sensible defaults.
    - Clamps confidence to [0, 1] and floors sub-``CONFIDENCE_FLOOR`` scores to 0
      (matches ``structured/semantics.CONFIDENCE_FLOOR`` semantics: below the
      floor, treat the proposal as "no answer" rather than a weak guess).
    - Flags whether each proposal came from existing vocabulary so the UI can
      distinguish "chose Alpha (existing)" from "invented Alpha (new)".
    """
    def _clamp(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        v = max(0.0, min(1.0, v))
        return v if v >= CONFIDENCE_FLOOR else 0.0

    def _in_vocab(facet: str, value) -> bool:
        if not value:
            return False
        existing = {e.get("value") for e in vocabulary.get(facet, []) if e.get("value")}
        return value in existing

    domain = str(raw.get("domain") or "general").strip() or "general"
    title = raw.get("title")
    area = raw.get("area")
    project = raw.get("project")
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t) for t in tags if t][:5]
    target_hint = raw.get("target_file_hint")

    domain_available = domain in set(available_domains) if available_domains else False

    return {
        "domain": {
            "value": domain,
            "confidence": _clamp(raw.get("domain_confidence")),
            "known": domain_available,
        },
        "title": {
            "value": str(title).strip() if title else None,
            "confidence": _clamp(raw.get("title_confidence")),
        },
        "area": {
            "value": str(area).strip() if area else None,
            "confidence": _clamp(raw.get("area_confidence")),
            "known": _in_vocab("area", area),
        },
        "project": {
            "value": str(project).strip() if project else None,
            "confidence": _clamp(raw.get("project_confidence")),
            "known": _in_vocab("project", project),
        },
        "tags": {
            "value": tags,
            "confidence": _clamp(raw.get("tags_confidence")),
            "known": [_in_vocab("tags", t) for t in tags],
        },
        "target_file_hint": (str(target_hint).strip() if target_hint else None),
        "reason": str(raw.get("reason") or "").strip(),
    }


def propose_placement(
    text: str,
    context: str | None = None,
    domains: Sequence[str] | None = None,
    model: str | None = None,
) -> dict:
    """Propose placement for a block of text (ADR 0012 A6).

    Never writes to the graph. Reads the controlled vocabulary via
    ``graph_query.filing_vocabulary`` (A5) and asks the configured LLM
    to pick a domain/title/area/project/tags proposal grounded in it. If the
    graph is unreachable the call still returns a proposal, just without
    vocabulary grounding (the LLM has to invent labels).

    Returns::

        {
          "text_length": int,
          "vocabulary": <A5 vocab result>,
          "proposals": <normalized proposal dict>,
          "raw": <LLM's raw JSON output>,
          "model": str,
        }
    """
    from artmind.extraction import call_llm
    from artmind.graph_query import filing_vocabulary
    from utils.functions import load_env

    if not text or not text.strip():
        raise ValueError("propose_placement: text is required")

    env = load_env()
    resolved_model = model or env.get("ARTMIND_KG_LLM_MODEL") or env.get("ARTMIND_LLM_MODEL") or "ministral-3:14b"

    try:
        vocab_result = filing_vocabulary(domains=list(domains) if domains else None)
        vocabulary = vocab_result.get("vocabulary", {"project": [], "area": [], "tags": [], "domain": []})
    except Exception as e:
        logger.warning("propose_placement: vocabulary lookup failed ({}); prompting without grounding", e)
        vocab_result = None
        vocabulary = {"project": [], "area": [], "tags": [], "domain": []}

    # Available domain labels: prefer the caller's explicit list, else fall
    # back to what the graph has already seen.
    if domains:
        available_domains = list(domains)
    else:
        available_domains = [e.get("value") for e in vocabulary.get("domain", []) if e.get("value")]
    if "general" not in available_domains:
        available_domains.append("general")

    prompt = build_prompt(text, vocabulary, available_domains, context=context)
    raw_llm = call_llm(resolved_model, prompt)
    raw = _parse_json_response(raw_llm)
    proposal = _normalize_proposal(raw, vocabulary, available_domains)

    return {
        "text_length": len(text),
        "vocabulary": vocabulary,
        "proposals": proposal,
        "raw": raw,
        "model": resolved_model,
    }
