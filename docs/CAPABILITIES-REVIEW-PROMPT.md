# Capability review — reusable prompt

Instructions for reviewing one section of [`CAPABILITIES.md`](CAPABILITIES.md) against the
artmind codebase. Written to be picked up cold, by any model, in any session.

**To invoke, paste:**

> Review section `<N>` of `docs/CAPABILITIES.md`, following `docs/CAPABILITIES-REVIEW-PROMPT.md`.

Substitute a section number (`2`), a subsection (`6.3`), or a single row (`4.5`).

---

## What this work is

`docs/CAPABILITIES.md` is a **feature baseline distilled from artmind as a reference
implementation**. It exists to be used twice: as the *input baseline* describing what a
knowledge system should offer, and as the *test checklist* for scoring other
implementations. Its Level-1 structure follows the CLI's own command groupings
(`COMMAND_GROUPS` in `artmind/cli.py`), with `ingest` deliberately split into three
capabilities (Ingestion / KG Construction / Refinement) because those are independently
testable.

The doc is built row by row. Each pass takes one section, verifies its claims against
source, and enriches it. **Rows are drafted from command surface first and grounded in
code second** — the `✓` column tracks which have had the second pass.

The markdown file is the only deliverable. Do not produce HTML, artifacts, or diagrams
beyond the mindmap already embedded in the doc.

## Before you start

1. **Read `docs/CAPABILITIES.md`** — at minimum the header, the section under review, and
   one existing `Grounding notes` block (section 1 has the worked examples) so you match
   the established voice and conventions.
2. **Read `CLAUDE.md`** for repo layout and the testing traps.
3. Run `just dev-cli-help` for the real command hierarchy. **Trust it over any prose** —
   group docstrings in this repo have drifted from reality before.

## The review pass

For each row in the section, in id order:

1. **Read the implementing code**, not just the command's help text. Start at the command
   in `artmind/cli.py`, then follow into the module that does the actual work.
2. **Grep the call sites** of whatever core helper you find. This is where the richest
   findings live — uniformity, enforcement, and reuse are invisible from a single
   definition. (The strongest finding of the section-1 pass came this way: the domain
   predicate is composed into *every* retrieval path including LLM-generated Cypher.)
3. **Read a real data artifact** when the feature is data-shaped — an actual schema YAML,
   a job record, a snapshot manifest — rather than inferring structure from code alone.
4. **Compare statement to reality.** Does the row over-claim, under-claim, or describe a
   mechanism that doesn't exist? Section 1 found a row claiming prompts were *generated
   from* the schema when the code merely prints a *stored* field.
5. **Look sideways for unlisted capabilities.** While in that code, note real features the
   map doesn't yet have a row for. Section 1 gained three rows this way.

### Watch for these repo-specific traps

- **Run folder vs checkout.** Paths like `DOMAIN_SCHEMAS_DIR` resolve under `$ARTMIND_HOME`
  (default `~/.artmind`), *not* the checkout. Files in `artmind/domains/schemas/` and
  `artmind/skills/` are package defaults seeded into the run folder — reading the checkout
  can mislead you about what is live.
- **A running daemon serves stale code.** Never verify behaviour through `artmind query`
  without `ARTMIND_NO_PROXY=1`.
- **Green tests prove little here.** The suite in `test/` (singular) is hermetic — no Neo4j,
  no network — and bypasses the entry-point proxy entirely.

## What to report, then stop

Report in prose, in this order. Then **stop and wait** — do not edit the doc yet.

1. **What the code actually does** — the mechanism behind the rows, stated plainly.
2. **Disconnects** — where a statement and the implementation disagree, and which is right.
3. **Hidden features surfaced** — capabilities found in code with no row yet, each with the
   anchor that would justify it.
4. **Proposed changes** — a concrete, numbered list: revised statements (quote the new
   wording), new rows with proposed ids, merges, splits, scoring notes.

Flag anything you could not verify rather than marking it grounded. Partial verification is
worth reporting as partial — section 1 left `1.4` ungrounded because the CLI command was
read but `harmonizer.py` was not.

## Applying changes

Only after the user responds. They may approve wholesale, revise wording, or reject rows.

**Conventions that must hold:**

- **Ids are stable.** Never renumber an existing row. New rows take the next free number in
  their section, even if that puts them out of thematic order.
- **Statements stay implementation-agnostic.** No artmind command names inside the Statement
  text — describe the behaviour so a differently-built system can score `full`. The
  artmind-specific part belongs in the Reference anchor column.
- **`✓` means the implementing code was read.** Not the help text, not the docstring.
- **Grounding notes carry exactly two facets**, in this order, with italic labels:
  - *Why it matters* — design intent, what the shape buys you, what it enables elsewhere.
    Load-bearing mechanism detail is welcome here when framed as consequence rather than
    as a code fact.
  - *Test hint* — what to actually verify when scoring another implementation.

  Write notes only for rows the pass covered. Not every row needs one.
- **Scoring notes** are blockquotes placed after a table, used when the reference
  implementation has a limitation that shouldn't weaken the baseline statement itself
  (e.g. section 1 notes that only a schema's `name` field is validated).
- **Sync the mindmap** at the top of the doc when a section gains or loses a theme.
- **Feature tables are 5 columns**: `# | ✓ | Feature | Statement | Reference anchor`. The
  comparison-matrix template at the bottom is 4 columns by design.

**Verify after editing:**

```bash
python3 -c "
import re
bad=[]
for i,l in enumerate(open('docs/CAPABILITIES.md'),1):
    l=l.rstrip()
    if re.match(r'^\| ?\d+(\.\d+)*\s*\|', l):
        n=len(l.split('|'))-2
        if n!=5: bad.append((i,n,l[:70]))
print('rows with wrong column count:', bad or 'none (the one 4-col row is the matrix template)')
print('grounded:', sum(1 for l in open('docs/CAPABILITIES.md') if re.match(r'^\| ?\d+(\.\d+)*\s*\| ✓', l)))
print('total rows:', sum(1 for l in open('docs/CAPABILITIES.md') if re.match(r'^\| ?\d+(\.\d+)*\s*\|', l)))
"
```

Close by reporting what changed, the new grounded/total tally, and which row is the next
unverified one.
