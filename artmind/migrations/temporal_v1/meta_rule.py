"""Step `meta_rule` (plan Task 34): the vault's `meta.yaml` gains
`identity_naming_rules.occasion`, the rule core's prompt builder needs for an
`identity: occasion` class (`MetaIncomplete` without it).

The file is edited as text, never re-dumped, so a vault's comments and layout survive
byte for byte. The edit is re-parsed and the original bytes restored if it fails; a
shape this step cannot edit safely (a non-empty inline mapping, a scalar) is refused
with a message rather than rewritten. A vault with no `meta.yaml` has nothing to
convert: `artmind init` seeds one from the package, which has the key.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

from paths import DOMAIN_META_PATH, PACKAGE_META_YAML

KEY = "identity_naming_rules"
RULE = "occasion"

_KEY_LINE = re.compile(rf"^{KEY}:[ \t]*(?P<rest>.*?)[ \t]*$")
_NULL = re.compile(r"^(?:~|null)(?:[ \t]*(#.*))?$")
_EMPTY_FLOW = re.compile(r"^\{\}(?:[ \t]*(#.*))?$")


class MetaRuleError(Exception):
    """The edit could not be made safely; nothing was written."""


def _load(path: Path) -> tuple[bytes, str, dict]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MetaRuleError(f"{path} is not UTF-8 ({exc.reason} at byte {exc.start}); fix its encoding first") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MetaRuleError(f"{path} does not parse as YAML; fix it first ({str(exc).splitlines()[0]})") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise MetaRuleError(f"{path} is not a mapping at its top level; fix it first")
    return raw, text, data


def _rules(data: dict):
    rules = data.get(KEY)
    return rules if isinstance(rules, dict) else None


def _packaged() -> tuple[list[str], object]:
    """The packaged `occasion:` entry (key line and continuations, indent 2) and its value."""
    text = PACKAGE_META_YAML.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln)), None)
    if start is None:
        raise MetaRuleError(f"the packaged meta.yaml has no {KEY}")
    block = []
    for ln in lines[start + 1:]:
        if ln.strip() and not ln[0].isspace():
            break  # the next top-level key
        block.append(ln)
    while block and not block[-1].strip():
        block.pop()
    if not block or not block[0].startswith(f"  {RULE}:"):
        raise MetaRuleError(f"the packaged {KEY} has no {RULE} entry")
    return block, yaml.safe_load(text)[KEY][RULE]


def _edited(text: str, data: dict) -> str:
    entry, _ = _packaged()
    rules = _rules(data)
    if KEY in data and rules is not None and RULE in rules:
        raise MetaRuleError(f"{KEY}.{RULE} is present in meta.yaml but empty; fill it by hand "
                            f"(see the packaged artmind/domains/meta.yaml)")
    lines = text.splitlines(keepends=True)
    eol = next((ln[len(ln.rstrip("\r\n")):] for ln in lines if ln.endswith(("\n", "\r"))), "\n")
    at = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln.rstrip("\r\n"))), None)
    if at is None:
        if KEY in data:
            raise MetaRuleError(f"{KEY} is written in a form this step cannot edit (a quoted key?); "
                                f"add an `{RULE}:` entry under it by hand, then re-run")
        body = text.rstrip("\r\n")
        return body + (eol * 2 if body else "") + eol.join([f"{KEY}:", *entry]) + eol
    rest = _KEY_LINE.match(lines[at].rstrip("\r\n")).group("rest")
    comment = ""
    if rest == "":
        pass
    elif rest.startswith("#"):
        comment = " " + rest
    elif m := _EMPTY_FLOW.match(rest) or _NULL.match(rest):
        comment = " " + m.group(1) if m.group(1) else ""
    else:
        raise MetaRuleError(
            f"{KEY} in meta.yaml is written inline ({rest[:40]!r}); add an `{RULE}:` entry under it by hand "
            f"(see the packaged artmind/domains/meta.yaml), then re-run"
        )
    lines[at] = f"{KEY}:{comment}{eol}"
    child = next((ln for ln in lines[at + 1:] if ln.strip() and not ln.lstrip().startswith("#")), "")
    if child.startswith("\t"):
        raise MetaRuleError(f"{KEY} in meta.yaml is indented with tabs; re-indent it with spaces, then re-run")
    indent = len(child) - len(child.lstrip(" ")) if child.startswith(" ") else 2
    pad = " " * indent
    if not lines[at].endswith(("\n", "\r")):
        lines[at] += eol
    lines[at + 1:at + 1] = [(pad + ln[2:] if ln.startswith("  ") else ln) + eol for ln in entry]
    return "".join(lines)


def _plan(path: Path) -> tuple[bytes, str | None]:
    """(original bytes, the edited text), or (bytes, None) when the rule is already there.
    Raises `MetaRuleError` for anything it cannot edit safely. Parses once."""
    raw, text, data = _load(path)
    rules = _rules(data)
    if rules is not None and rules.get(RULE):
        return raw, None
    new = _edited(text, data)
    _, value = _packaged()
    expected = {**data, KEY: {**(rules or {}), RULE: value}}
    try:
        got = yaml.safe_load(new)
    except yaml.YAMLError:
        got = None
    if got != expected:
        raise MetaRuleError(f"{path}: the edit would change more than {KEY}.{RULE}; nothing was written. "
                            f"Add the entry by hand (see the packaged artmind/domains/meta.yaml)")
    return raw, new


def pending(ctx) -> dict:
    """`{"meta": 1}` when the vault's meta.yaml exists and lacks the rule."""
    path = Path(DOMAIN_META_PATH)
    if not path.is_file():
        return {}
    _, text, data = _load(path)
    rules = _rules(data)
    return {} if rules is not None and rules.get(RULE) else {"meta": 1}


def writes(ctx) -> list[Path]:
    path = Path(DOMAIN_META_PATH)
    try:
        return [path] if pending(ctx) else []
    except MetaRuleError:
        return [path]  # unreadable: the run reports why; the file is still the one at stake


def apply(ctx, dry_run: bool = False) -> dict:
    path = Path(DOMAIN_META_PATH)
    if dry_run or not path.is_file():
        return {}
    _, new = _plan(path)
    if new is None:
        return {}
    target = path.resolve()  # a symlinked meta.yaml stays a symlink
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".meta.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new.encode("utf-8"))
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {"meta": 1}
