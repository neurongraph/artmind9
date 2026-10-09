---
name: regulation_exposure
description: Use when asked which regulations apply to a product, directly or through its parts or parent products. Not for a single product's direct obligations (use pattern2) or for comparing two products.
---

# regulation_exposure

## When to use / when not to

Use for "which regulations apply to <product>?" and "what governs <product>, even
indirectly?". If the user only wants the obligations stated on one product's own
page, `query graph pattern2` is enough and cheaper. If the question is about a
regulation's text (what it requires), use this view to find it, then Ground.

## Output

One row per regulation. `regulation` is its name; `hops` is the shortest chain of
PART_OF / GOVERNED_BY links from the product (1 = direct).

## Presenting the answer

Lead with the direct regulations (`hops` = 1), then say how many more apply
indirectly and show those grouped by distance. If more than 15 rows came back,
summarise and show the first 10.

## Grounding

Pass the `_id` values to `query entity-context` to quote the source text behind a
regulation. Always state which product the name resolved to.

## Known limits

Follows only PART_OF and GOVERNED_BY; regulations linked by other relationship types
are not found. Depth is capped at 6.
