---
name: org_hierarchy
description: Use for "who reports to X" questions that want the whole tree below a person or unit. For one level only ("X's direct reports"), query graph pattern3 is enough.
---

# org_hierarchy

## When to use / when not to

Use when the user wants the reporting tree below someone, several levels deep. Not for
"who is X's manager" (one hop up) and not for headcount totals (count the rows).

## Output

One row per person at or below the root: `_id`, `name`, and `parent` (the `_id` of
their manager; empty for the root).

## Presenting the answer

Pass `rendered` through as the org chart. Add one sentence with the headcount
(`rows_total`) and the number of levels. If `truncated`, say the tree is cut off and
suggest a smaller `max_depth` or a lower root.

## Grounding

Use `query entity-context` on a person's `_id` for their role and sources.

## Known limits

Assumes the REPORTS_TO relationship points from the report to the manager. A person with
two managers appears once, under the closer one. Depth is capped at 4.
