"""Text-level frontmatter edits for human notes (spec 2026-09-26 §5 R6).

artmind writes a human note only to stamp its identity -- `_artmind_id` and
`_domain`, once, and again only when `_domain` genuinely changes -- and,
once per vault, when `artmind vault migrate-frontmatter` removes the
per-ingest fields older artmind wrote there. Each edit here changes only the
frontmatter lines it has to:

- every byte after the closing `---` (the body) is left as it is;
- the user's own keys keep their text, order, comments and quoting;
- a line artmind adds uses the file's own line ending (LF or CRLF).

A YAML round trip (parse, then dump) would reorder keys, re-quote values and
drop comments, so none is done. Every edit is checked instead: the edited
block must parse to exactly the mapping expected, or `FrontmatterEditError`
is raised and the caller writes nothing.

"Frontmatter" is recognised exactly as `ingest._parse_md_frontmatter` does:
the text starts with `---`, and the block runs to the next line that starts
with `---`.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

import yaml

#: A scratch file's suffix -- `.gitignore` block v4 ignores it under
#: `.artmind/data/` (spec R2, R4).
TMP_SUFFIX = ".artmind-tmp"


class FrontmatterEditError(ValueError):
    """The frontmatter could not be edited without touching more than the
    lines asked for (unparsable YAML, not a mapping, or a key written in a
    form the line editor does not recognise). Nothing was written."""


def _split(text: str) -> tuple[str, str, str] | None:
    """`(head, block, tail)`: `head` is the opening `---`, `block` runs from
    right after it through the newline before the closing `---`, and `tail`
    starts at the closing `---`. None when the text has no frontmatter."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[:3], text[3:end + 1], text[end + 1:]


def _lines(block: str) -> list[str]:
    """`block` as lines, each with its own ending. Only `\\n` splits a line
    (unlike `str.splitlines`), so no other character can move a byte."""
    return re.findall(r"[^\n]*\n|[^\n]+$", block)


def _eol(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else "\n"


def _load(block: str) -> dict:
    try:
        data = yaml.safe_load(block)
    except Exception as e:  # PyYAML also leaks ValueError/AttributeError (an impossible date, `!!timestamp`)
        raise FrontmatterEditError(f"frontmatter is not valid YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterEditError("frontmatter is not a YAML mapping")
    return data


def _key_line(key: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(key)}[ \t]*:(?=[ \t\r\n]|$)")


def _is_continuation(line: str) -> bool:
    """A line belonging to the key above it: indented (a folded scalar, a
    nested mapping, an indented list) or a block-sequence entry at column 0
    (`tags:` followed by `- a`)."""
    return line.startswith((" ", "\t")) or re.match(r"-(?=[ \t\r\n]|$)", line) is not None


def _key_span(lines: list[str], key: str) -> tuple[int, int] | None:
    pattern = _key_line(key)
    for i in range(1, len(lines)):
        if pattern.match(lines[i]):
            j = i + 1
            while j < len(lines) and _is_continuation(lines[j]):
                j += 1
            return i, j
    return None


def _render(key: str, value: object, eol: str) -> str:
    dumped = yaml.safe_dump(
        {key: value}, allow_unicode=True, default_flow_style=False, sort_keys=False, width=float("inf"),
    )
    return dumped.replace("\n", eol)


def _refuse_bom(text: str) -> None:
    # `ingest._parse_md_frontmatter` does not see frontmatter behind a BOM, so
    # neither must the editor: without this, a BOM note's own properties would
    # be treated as body and a second block written in front of them.
    if text.startswith("\ufeff"):
        raise FrontmatterEditError(
            "the note starts with a byte-order mark (BOM), which artmind does not read frontmatter "
            "behind -- save it without one (UTF-8, no BOM) and try again"
        )


def has_frontmatter(text: str) -> bool:
    return _split(text) is not None


def set_fields(text: str, updates: dict[str, object]) -> str:
    """`text` with each key in `updates` set to its value.

    A key already present with that value is left exactly as written; one
    present with another value has its line(s) replaced in place; a missing
    key is inserted at the top of the block, in `updates` order. A note with
    no frontmatter gets a new block holding just `updates`, and every byte of
    the note follows it unchanged."""
    _refuse_bom(text)
    split = _split(text)
    if split is None:
        first_line = _lines(text)[0] if text else "\n"
        eol = _eol(first_line)
        block = eol + "".join(_render(k, v, eol) for k, v in updates.items())
        if _load(block) != dict(updates):
            raise FrontmatterEditError(f"could not render {sorted(updates)} as frontmatter")
        return "---" + block + "---" + eol + text

    head, block, tail = split
    before = _load(block)
    lines = _lines(block)
    eol = _eol(lines[0]) if lines else "\n"
    if not lines:
        lines = [eol]
    missing = []
    for key, value in updates.items():
        if key in before and before[key] == value:
            continue
        span = _key_span(lines, key)
        if span is None:
            missing.append(key)
            continue
        i, j = span
        lines[i:j] = [_render(key, value, eol)]
    lines[1:1] = [_render(key, updates[key], eol) for key in missing]
    new_block = "".join(lines)
    if _load(new_block) != {**before, **updates}:
        raise FrontmatterEditError(f"setting {sorted(updates)} would change other frontmatter keys")
    return head + new_block + tail


def remove_fields(text: str, keys) -> str:
    """`text` without the top-level `keys` (each with its continuation
    lines). Keys that are not there are ignored; a note with no frontmatter
    comes back unchanged."""
    if isinstance(keys, str):
        raise TypeError("keys must be a collection of key names, not a single string")
    _refuse_bom(text)
    split = _split(text)
    if split is None:
        return text
    head, block, tail = split
    before = _load(block)
    doomed = [k for k in keys if k in before]
    if not doomed:
        return text
    lines = _lines(block)
    for key in doomed:
        span = _key_span(lines, key)
        if span is not None:
            del lines[span[0]:span[1]]
    new_block = "".join(lines)
    expected = {k: v for k, v in before.items() if k not in doomed}
    if _load(new_block) != expected:
        raise FrontmatterEditError(
            f"could not remove {sorted(doomed)} line by line (a key written in a form artmind "
            "does not recognise?) -- remove them by hand"
        )
    return head + new_block + tail


def read_note(path: Path) -> str:
    """A note's text exactly as stored -- no newline translation, so a CRLF
    note stays CRLF through an edit."""
    return Path(path).read_bytes().decode("utf-8")


def _scratch_dirs(path: Path, scratch_dir: Path | None) -> list[Path]:
    """Where to try the temp file, in order. `scratch_dir` is the vault's
    `.artmind/data`: it is created only when its parent (`.artmind`) exists,
    so a note outside a vault never gets a stray folder made for it."""
    dirs = []
    if scratch_dir is not None:
        scratch_dir = Path(scratch_dir)
        if scratch_dir.is_dir() or scratch_dir.parent.is_dir():
            dirs.append(scratch_dir)
    return dirs + [path.parent]


def write_note(path: Path, text: str, *, scratch_dir: Path | None = None) -> None:
    """Replace `path` with `text` atomically: a temp file, fsynced, renamed
    over it -- an interrupted write leaves the old note or the new one, never
    half of either. The temp file is created in `scratch_dir` (the vault's
    `.artmind/data/`, where `.gitignore` ignores `*.artmind-tmp`) when that folder exists or
    can be made under an existing `.artmind/`, else beside the note; it is
    created with the note's own permission bits, so a private note is never
    briefly readable by others. A symlinked note is written through, the link
    left in place."""
    path = Path(os.path.realpath(path))
    data = text.encode("utf-8")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        mode = 0o666            # a new note: the usual permissions, less the umask
    for directory in _scratch_dirs(path, scratch_dir):
        directory.mkdir(exist_ok=True)
        tmp = directory / f".{uuid.uuid4().hex}{TMP_SUFFIX}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
            os.replace(tmp, path)
            return
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
        finally:
            tmp.unlink(missing_ok=True)
    raise OSError(errno.EXDEV, f"could not rename a temp file over {path}")
