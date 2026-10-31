# artmind/retrieval_eval/generate.py
"""Synthetic retrieval labels (spec 2026-10-31 §6.2): sample chunks, ask the
configured LLM for one question each. The sampled text goes to the provider
(logged in llm_calls.log, like extraction). From a Claude Code shell, run with
`env -u ANTHROPIC_BASE_URL` (CLAUDE.md, Testing implications §5).
"""
from __future__ import annotations

import random
from collections.abc import Callable

from artmind.retrieval_eval.labels import RetrievalLabel

MIN_CHARS = 200
SAMPLE_CHARS = 3000

QUESTION_PROMPT = """You write one evaluation question for a document search system.

Below is a passage from a document. Write ONE question that a person could ask
and that this passage answers. Rules:
- Paraphrase: do not copy distinctive phrases, section titles or rare words from
  the passage unless the question cannot be asked without them (a person's,
  product's or organisation's name is fine).
- The question must make sense to someone who has not seen the passage.
- Answer with JSON only: {{"question": "..."}}

<passage>
{text}
</passage>
"""


def sample_chunks(session, domain: str, count: int, seed: int) -> list[dict]:
    rows = session.run(
        """
        MATCH (c:DocChunk)
        WHERE c._domain = $domain OR c._domain STARTS WITH ($domain + '.')
        RETURN c.id AS id, c.doc_id AS doc_id, c.text AS text
        ORDER BY c.id
        """,
        domain=domain,
    ).data()
    rows = [r for r in rows if len((r.get("text") or "").strip()) >= MIN_CHARS]
    return random.Random(seed).sample(rows, min(count, len(rows)))


def _default_llm() -> Callable[[str], str]:
    from artmind.extraction import call_llm
    from utils.functions import load_env, resolve_llm_model

    model = resolve_llm_model(load_env())
    return lambda prompt: call_llm(model, prompt, call_type="retrieval_label")


def generate_labels(
    session, domain: str, count: int, seed: int, *, llm: Callable[[str], str] | None = None
) -> tuple[list[RetrievalLabel], list[str]]:
    from artmind.extraction import parse_json_response

    llm = llm or _default_llm()
    labels: list[RetrievalLabel] = []
    warnings: list[str] = []
    for row in sample_chunks(session, domain, count, seed):
        prompt = QUESTION_PROMPT.format(text=row["text"][:SAMPLE_CHARS])
        try:
            parsed = parse_json_response(llm(prompt))
        except Exception as exc:  # provider error or unparseable answer
            warnings.append(f"{row['id']}: no question ({exc})")
            continue
        question = str((parsed or {}).get("question") or "").strip() if isinstance(parsed, dict) else ""
        if not question:
            warnings.append(f"{row['id']}: empty question")
            continue
        labels.append(RetrievalLabel(id="", question=question, domains=(domain,), kind="needle",
                                     relevant=(row["id"],), source="synthetic"))
    return labels, warnings
