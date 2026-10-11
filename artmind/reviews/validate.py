"""Completeness rules for a review dossier (spec 2026-10-11 §7.2, §8.2,
§8.3). `validate` never raises on a malformed dossier; it reports."""

import re

from artmind.reviews.dossier import PRIVACY_PREAMBLE, SECTIONS, STAGES, STATUSES, Dossier

GRAPH_SHAPE_STAGES = frozenset({"identity", "extraction", "schema"})
_ELEMENT_RE = re.compile(r"^[\w.\-]+/\w+(\.\w+|-\w+->)?$")
_PLACEHOLDER_RE = re.compile(r"<!--.*?-->", re.S)
_ALWAYS = ("Symptom", "Diagnosis", "Proposal", "Regression question", "Agent prompt", "Log")


def filled(text: str | None) -> bool:
    return bool(_PLACEHOLDER_RE.sub("", text or "").strip())


def validate(dossier: Dossier) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    meta, sections = dossier.meta, dossier.sections

    if meta.get("status") not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}; got {meta.get('status')!r}")
    stage = meta.get("stage")
    if stage not in STAGES:
        errors.append(f"stage must be one of {', '.join(STAGES)}; got {stage!r}")
    trace_ids = meta.get("trace_ids")
    if not isinstance(trace_ids, list) or not trace_ids:
        errors.append("trace_ids must list at least one trace")
    elements = meta.get("elements") or []
    if not isinstance(elements, list):
        errors.append("elements must be a list")
        elements = []
    for element in elements:
        if not _ELEMENT_RE.match(str(element)):
            errors.append(f"element {element!r} must look like domain/Class, domain/Class.property or domain/Class-REL->")
    if stage in GRAPH_SHAPE_STAGES and not elements:
        errors.append("an identity, extraction or schema verdict must name at least one schema element")
    if not meta.get("private_terms"):
        warnings.append("private_terms is empty: review leakcheck can then match only quoted text")

    for title in SECTIONS:
        if title not in sections:
            errors.append(f"section '{title}' is missing")
    required = list(_ALWAYS)
    if stage in GRAPH_SHAPE_STAGES:
        required.append("Shape audit")
    if stage != "corpus-gap":
        required.append("Synthetic example")
    for title in required:
        if title in sections and not filled(sections[title]):
            errors.append(f"section '{title}' is empty")
    if stage in GRAPH_SHAPE_STAGES | {"missing-view"} and not filled(sections.get("Proof")):
        warnings.append("section 'Proof' is empty: add `views test` output, or a re-extraction diff once P3 ships")

    if "Agent prompt" in sections and not sections["Agent prompt"].lstrip().startswith(PRIVACY_PREAMBLE):
        errors.append("the Agent prompt must start with the privacy preamble (artmind.reviews.dossier.PRIVACY_PREAMBLE)")

    if "Regression question" in sections:
        from artmind.benchmark import _parse_questions

        questions = _parse_questions(_PLACEHOLDER_RE.sub("", sections["Regression question"]))
        if len(questions) != 1 or not filled(questions[0].get("eval_comment")):
            errors.append("the Regression question must hold exactly one '### R-… / **Question:** / "
                          "**Evaluation comment:**' block with a filled evaluation comment")

    return {"review_id": meta.get("review_id"), "ok": not errors, "errors": errors, "warnings": warnings}
