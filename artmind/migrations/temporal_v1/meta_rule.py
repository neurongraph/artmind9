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
import tempfile
from pathlib import Path

import yaml

from paths import DOMAIN_META_PATH, PACKAGE_META_YAML

KEY = "identity_naming_rules"
RULE = "occasion"

_KEY_LINE = re.compile(rf"^{KEY}:[ \t]*(?P<rest>.*?)[ \t]*$")


class MetaRuleError(Exception):
    """The edit could not be made safely; nothing was written."""


def _has_rule(text: str) -> bool:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    rules = data.get(KEY) if isinstance(data, dict) else None
    return isinstance(rules, dict) and bool(rules.get(RULE))


def _packaged_entry() -> list[str]:
    """The packaged `occasion:` entry (its key line and continuation lines), at indent 2."""
    lines = PACKAGE_META_YAML.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln)), None)
    if start is None:
        raise MetaRuleError(f"the packaged meta.yaml has no {KEY}")
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and not ln[0].isspace():
            break  # the next top-level key
        block.append(ln)
    while block and not block[-1].strip():
        block.pop()
    entry = block[1:]
    if not entry or not entry[0].startswith(f"  {RULE}:"):
        raise MetaRuleError(f"the packaged {KEY} has no {RULE} entry")
    return entry


def _edited(text: str) -> str:
    entry = _packaged_entry()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    at = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln)), None)
    if at is None:
        # Append the whole block, one blank line after the last content.
        body = text.rstrip("\r\n")
        return body + nl + nl + nl.join([f"{KEY}:", *entry]) + nl
    match = _KEY_LINE.match(lines[at])
    rest = match.group("rest")
    comment = ""
    if rest.startswith("#"):
        comment, rest = " " + rest, ""
    if flow := re.match(r"^\{\}(?:[ \t]*(#.*))?$", rest):
        comment = " " + flow.group(1) if flow.group(1) else ""
        lines[at] = f"{KEY}:{comment}"
    elif rest not in ("", "~", "null"):
        raise MetaRuleError(
            f"{KEY} in meta.yaml is written inline ({rest[:40]!r}); add an `{RULE}:` entry under it by hand "
            f"(see the packaged artmind/domains/meta.yaml), then re-run"
        )
    elif rest in ("~", "null"):
        lines[at] = f"{KEY}:{comment}"
    # Match the existing children's indent when there are any.
    child = next((ln for ln in lines[at + 1:] if ln.strip() and not ln.lstrip().startswith("#")), "")
    indent = len(child) - len(child.lstrip()) if child.startswith((" ", "\t")) else 2
    pad = " " * indent
    shifted = [pad + ln[2:] if ln.startswith("  ") else ln for ln in entry]
    lines[at + 1:at + 1] = shifted
    return nl.join(lines)


def pending(ctx) -> dict:
    """`{"meta": 1}` when the vault's meta.yaml exists and lacks the rule."""
    path = Path(DOMAIN_META_PATH)
    if not path.is_file():
        return {}
    return {} if _has_rule(path.read_text(encoding="utf-8")) else {"meta": 1}


def writes(ctx) -> list[Path]:
    return [Path(DOMAIN_META_PATH)] if pending(ctx) else []


def apply(ctx, dry_run: bool = False) -> dict:
    if dry_run or not pending(ctx):
        return {}
    path = Path(DOMAIN_META_PATH)
    original = path.read_bytes()
    new = _edited(original.decode("utf-8"))
    if not _has_rule(new):
        raise MetaRuleError(f"{path}: the edit did not produce a valid {KEY}.{RULE}; nothing was written")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".meta.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    try:
        if not _has_rule(path.read_text(encoding="utf-8")):
            raise MetaRuleError("the file no longer holds the rule after writing")
    except Exception:
        path.write_bytes(original)
        raise
    return {"meta": 1}
