"""Schema harmonizer: sync child domain schemas against their parent."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from paths import DOMAIN_SCHEMAS_DIR


@dataclass
class HarmonizeResult:
    domain: str
    status: str           # "in_sync" | "updated" | "dry_run" | "error"
    added: list[str] = field(default_factory=list)
    error: str = ""


# ── block extraction ──────────────────────────────────────────────────────────


def _true_section_end(prompt: str, marker: str) -> int:
    """Position of `marker` in `prompt`, backed up over any banner line (a
    line of box-drawing '-' characters) immediately preceding it.

    Without this, the LAST header's span would run through `marker` and
    swallow that banner line into its block text -- harmless for a schema
    that's merely read, but corrupting when that block gets copied verbatim
    into another file (as `harmonize_schema` does): the stray banner line
    would land mid-prompt in every child that inherits that entity class.
    """
    idx = prompt.find(marker)
    if idx == -1:
        return len(prompt)
    m = re.search(r'\n━+$', prompt[:idx])
    return m.start() if m else idx


def _split_entity_blocks(prompt: str) -> dict[str, str]:
    """Return {ENTITY_TYPE: block_text} from entities_prompt.

    Each block runs from the type header line up to (not including) the next
    header or the EXTRACTION RULES separator.

    Header lines are unindented (column 0) and body lines indented 2 spaces —
    this matches how every real `*_schema.yaml` in domains/schemas/ is written
    once YAML dedents the `entities_prompt: |` block scalar (verified against
    banking_*, fiction, general, contracts, etc.), not one level deeper.
    """
    header_re = re.compile(r'^([A-Z_][A-Z0-9_]*)$', re.MULTILINE)
    headers = list(header_re.finditer(prompt))
    section_end_idx = _true_section_end(prompt, '\nEXTRACTION RULES:')

    result: dict[str, str] = {}
    for i, match in enumerate(headers):
        start = match.start()
        if start >= section_end_idx:
            break
        next_start = headers[i + 1].start() if i + 1 < len(headers) else section_end_idx
        end = min(next_start, section_end_idx)
        result[match.group(1)] = prompt[start:end].rstrip()
    return result


def _split_property_blocks(prompt: str) -> dict[str, str]:
    """Return {ENTITY_TYPE: block_text} from properties_prompt.

    Header lines are unindented (column 0), matching real schema files (see
    `_split_entity_blocks` docstring).
    """
    header_re = re.compile(r'^For ([A-Z_][A-Z0-9_]*), consider:$', re.MULTILINE)
    headers = list(header_re.finditer(prompt))
    key_rules_idx = _true_section_end(prompt, '\nKEY RULES FOR PROPERTIES:')

    result: dict[str, str] = {}
    for i, match in enumerate(headers):
        start = match.start()
        next_start = headers[i + 1].start() if i + 1 < len(headers) else key_rules_idx
        end = min(next_start, key_rules_idx)
        result[match.group(1)] = prompt[start:end].rstrip()
    return result


def _split_relationship_blocks(prompt: str) -> dict[str, list[str]]:
    """Return {ENTITY_TYPE: [block, ...]} from relationships_prompt.

    Each entity type maps to ALL relationship blocks it appears in (both sides of ↔).

    Header lines are unindented (column 0) and body lines indented 2 spaces,
    matching real schema files (see `_split_entity_blocks` docstring).
    """
    block_re = re.compile(
        r'^([A-Z_][A-Z0-9_]*) ↔ ([A-Z_][A-Z0-9_]*):\n(?:  [^\n]+\n?)+',
        re.MULTILINE,
    )
    result: dict[str, list[str]] = {}
    for m in block_re.finditer(prompt):
        block = m.group(0).rstrip()
        t1, t2 = m.group(1), m.group(2)
        result.setdefault(t1, []).append(block)
        if t2 != t1:
            result.setdefault(t2, []).append(block)
    return result


# ── injection helpers ─────────────────────────────────────────────────────────


def _insert_before(text: str, marker: str, content: str) -> str:
    """Insert `content` immediately before `marker` in `text`. Appends if not found.

    Backs up over a banner line (box-drawing '-' characters) immediately
    preceding `marker`, so injected content lands before that banner rather
    than being sandwiched between the banner and the marker.
    """
    idx = _true_section_end(text, marker)
    if idx == len(text) and text.find(marker) == -1:
        return text + '\n\n' + content
    return text[:idx] + content + '\n\n' + text[idx:]


def _inject_entity_blocks(prompt: str, blocks: list[str]) -> str:
    """Append entity blocks before the EXTRACTION RULES separator."""
    return _insert_before(prompt, '\nEXTRACTION RULES:', '\n\n' + '\n\n'.join(blocks))


def _inject_property_blocks(prompt: str, blocks: list[str]) -> str:
    """Append property blocks before the KEY RULES section."""
    return _insert_before(prompt, '\nKEY RULES FOR PROPERTIES:', '\n\n' + '\n\n'.join(blocks))


def _inject_relationship_blocks(prompt: str, blocks: list[str]) -> str:
    """Append relationship blocks before the EXTRACTION RULES separator."""
    return _insert_before(prompt, '\nEXTRACTION RULES:', '\n\n' + '\n\n'.join(blocks))


# ── core harmonize logic ──────────────────────────────────────────────────────


def _load_schema_raw(schema_path: Path) -> tuple[dict, str]:
    """Return (parsed_dict, raw_file_text)."""
    raw = schema_path.read_text(encoding='utf-8')
    return yaml.safe_load(raw), raw


def _write_schema(schema_path: Path, data: dict, raw: str) -> None:
    """Write updated schema back, preserving YAML literal block scalars.

    Strategy: update the three prompt fields by string-replacing their content
    in the raw file, and patch the entity_types list via raw text replacement.
    The rest of the file (name, description) is unchanged.
    """
    new_types_block = 'entity_types:\n' + ''.join(
        f'  - {t}\n' for t in data['entity_types']
    )
    et_re = re.compile(r'^entity_types:\n(?:  - [^\n]+\n)+', re.MULTILINE)
    if et_re.search(raw):
        raw = et_re.sub(new_types_block, raw)
    else:
        raw = raw.replace('\nentities_prompt:', '\n' + new_types_block + '\nentities_prompt:')

    for key in ('entities_prompt', 'properties_prompt', 'relationships_prompt'):
        if key not in data:
            continue  # child never defined this section (e.g. a minimal stub) -- leave it absent
        new_val = data[key]
        section_re = re.compile(
            r'^(' + key + r': \|)\n((?:[ \t][^\n]*\n|\n)*)',
            re.MULTILINE,
        )

        def _replacer(m, val=new_val):
            indented = '\n'.join('  ' + ln if ln else '' for ln in val.splitlines())
            return m.group(1) + '\n' + indented + '\n'

        raw = section_re.sub(_replacer, raw, count=1)

    schema_path.write_text(raw, encoding='utf-8')


def harmonize_schema(
    child_name: str,
    dry_run: bool = False,
) -> HarmonizeResult:
    """Harmonize a single child schema against its parent."""
    parent_name = child_name.rsplit('.', 1)[0]
    child_path = DOMAIN_SCHEMAS_DIR / f'{child_name}_schema.yaml'
    parent_path = DOMAIN_SCHEMAS_DIR / f'{parent_name}_schema.yaml'

    if not child_path.exists():
        return HarmonizeResult(child_name, 'error', error=f'Child schema not found: {child_path}')
    if not parent_path.exists():
        return HarmonizeResult(child_name, 'error', error=f'Parent schema not found: {parent_path}')

    child, child_raw = _load_schema_raw(child_path)
    parent, _ = _load_schema_raw(parent_path)

    parent_types = set(parent.get('entity_types', []))
    child_types = set(child.get('entity_types', []))
    missing = parent_types - child_types

    if not missing:
        return HarmonizeResult(child_name, 'in_sync')

    # Defensive: a child schema may be a deliberately minimal stub that omits
    # properties_prompt/relationships_prompt entirely (e.g. banking_cases). Only
    # attempt to inject into a section the child actually defines, so harmonizing
    # such a domain grows its entities_prompt with the missing common classes
    # without fabricating sections it never had.
    parent_entity_blocks = _split_entity_blocks(parent.get('entities_prompt', ''))
    parent_prop_blocks = _split_property_blocks(parent.get('properties_prompt', ''))
    parent_rel_blocks = _split_relationship_blocks(parent.get('relationships_prompt', ''))

    entities_to_add = (
        [parent_entity_blocks[t] for t in sorted(missing) if t in parent_entity_blocks]
        if 'entities_prompt' in child else []
    )
    props_to_add = (
        [parent_prop_blocks[t] for t in sorted(missing) if t in parent_prop_blocks]
        if 'properties_prompt' in child else []
    )
    rels_to_add = []
    if 'relationships_prompt' in child:
        seen_rel_blocks: set[str] = set()
        for t in sorted(missing):
            for block in parent_rel_blocks.get(t, []):
                if block not in seen_rel_blocks:
                    rels_to_add.append(block)
                    seen_rel_blocks.add(block)

    if dry_run:
        return HarmonizeResult(child_name, 'dry_run', added=sorted(missing))

    if entities_to_add:
        child['entities_prompt'] = _inject_entity_blocks(child['entities_prompt'], entities_to_add)
    if props_to_add:
        child['properties_prompt'] = _inject_property_blocks(child['properties_prompt'], props_to_add)
    if rels_to_add:
        child['relationships_prompt'] = _inject_relationship_blocks(child['relationships_prompt'], rels_to_add)

    child['entity_types'] = sorted(child_types | missing)

    _write_schema(child_path, child, child_raw)

    return HarmonizeResult(child_name, 'updated', added=sorted(missing))


def harmonize_all(dry_run: bool = False) -> list[HarmonizeResult]:
    """Harmonize all child schemas found in DOMAIN_SCHEMAS_DIR."""
    results = []
    for schema_file in sorted(DOMAIN_SCHEMAS_DIR.glob('*_schema.yaml')):
        data = yaml.safe_load(schema_file.read_text(encoding='utf-8'))
        name = data.get('name', '')
        if '.' in name:
            results.append(harmonize_schema(name, dry_run=dry_run))
    return results
