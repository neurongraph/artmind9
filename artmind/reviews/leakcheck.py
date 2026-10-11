"""A backstop against copying a dossier's private evidence into the public
code checkout (spec 2026-10-11 §8.3). Read-only git: `diff` and `ls-files`.

Private = the dossier's `private_terms` (whole word, case-insensitive) and
its quoted text: blockquote lines in Symptom / Diagnosis / Shape audit and
the source-sentence column of the Shape audit table. An added line is a hit when it contains a term
or shares a 5-word sequence with the quoted text. Reviewer prose outside
quotes is not private, so a coding agent may paraphrase the diagnosis.
"""

import re
from pathlib import Path

from utils.functions import run_command

from artmind.reviews.dossier import Dossier

NGRAM = 5
MIN_TERM = 3
MAX_HITS = 200
MAX_FILE_BYTES = 1_000_000
QUOTED_SECTIONS = ("Symptom", "Diagnosis", "Shape audit")
_WORD = re.compile(r"\w+")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SKIP_PREFIX = ".artmind/reviews/"


class LeakcheckError(RuntimeError):
    pass


def _tokens(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def _ngrams(tokens: list[str]) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + NGRAM]) for i in range(len(tokens) - NGRAM + 1)}


def quoted_text(dossier: Dossier) -> str:
    lines = []
    for title in QUOTED_SECTIONS:
        for line in (dossier.sections.get(title) or "").split("\n"):
            stripped = line.strip()
            if stripped.startswith(">"):
                lines.append(stripped.lstrip("> "))
            elif title == "Shape audit" and stripped.startswith("|") and not set(stripped) <= set("|-: "):
                # Only the source-sentence column is evidence; the element and
                # check cells and the header row are structure every dossier
                # (and every synthetic test of one) shares.
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cells) >= 3 and cells[0].strip("` ").lower() != "element":
                    lines.append(cells[2])
    return "\n".join(lines)


def _git(repo: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=repo)
    if rc != 0:
        raise LeakcheckError(f"git {' '.join(args)} failed: {err.strip() or out.strip()}")
    return out


def added_lines(repo: Path, base: str = "HEAD"):
    """(path, line number, text) for every line added vs `base`, plus every
    line of untracked, non-ignored files."""
    current, number = None, 0
    for line in _git(repo, ["-c", "core.quotePath=false", "diff", "--no-color", "--unified=0", base]).split("\n"):
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif (hunk := _HUNK.match(line)):
            number = int(hunk.group(1))
        elif line.startswith("+") and current is not None:
            yield current, number, line[1:]
            number += 1
    for rel in filter(None, _git(repo, ["-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"]).split("\0")):
        path = Path(repo) / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, content in enumerate(text.split("\n"), 1):
            yield rel, i, content


def leakcheck(dossier: Dossier, repo: Path | str, base: str = "HEAD") -> dict:
    repo = Path(repo)
    terms = [t for t in dossier.meta.get("private_terms") or [] if isinstance(t, str) and len(t.strip()) >= MIN_TERM]
    patterns = [(t, re.compile(rf"(?<!\w){re.escape(t.strip())}(?!\w)", re.I)) for t in terms]
    grams = _ngrams(_tokens(quoted_text(dossier)))
    hits: list[dict] = []
    for rel, number, text in added_lines(repo, base):
        if rel.startswith(_SKIP_PREFIX):
            continue
        for term, pattern in patterns:
            if pattern.search(text):
                hits.append({"file": rel, "line": number, "kind": "term", "match": term})
        for gram in _ngrams(_tokens(text)):
            if gram in grams:
                hits.append({"file": rel, "line": number, "kind": "quote", "match": " ".join(gram)})
                break
    return {"review_id": dossier.meta.get("review_id"), "repo": str(repo), "base": base,
            "ok": not hits, "hit_count": len(hits), "hits": hits[:MAX_HITS]}
