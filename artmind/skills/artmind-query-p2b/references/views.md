# Saved views
Read when orient lists a view whose `summary` or `examples` fit the question.

### 1. Views — check for a saved view first

A **view** is a reviewed, parameterised, read-only graph query that a domain owner saved
for a question that keeps coming up, together with guidance on how to present it. A
matching view replaces Discover, Resolve and Retrieve: one call instead of several.

The views come from `orient --domain`: match **every** new question against them, not only the first.

A question **matches** a view when the view's `summary` or `examples` describe the same
kind of question, even in different words or about a different entity: the `params` are
there to carry the entity, date or name the user gave. "Who reports to Priya?" matches a
view whose example is "show me Alex's team". When in doubt, `views show` it and let its
`skill_md` say whether it fits — that is one cheap call.

If the question matches a view:

1. `artmind query views show <name> --compact` and follow the view's `skill_md`: it says
   when the view is the wrong tool, what each column means, and how to present the result.
2. Run it, passing one `--param name=value` per entry in the view's `params`:

   ```bash
   artmind query views run <name> --domain <domain> --param <p>=<value> --render markdown --compact
   ```

   If you already resolved the entity, pass the id as `--param <p>=@<_id>`:
   an `@` value skips name resolution. Otherwise give the name as the user said it; the
   view resolves it.
3. Read `status`:
   - `ok`: state which entity each `params` entry resolved to (so the user can catch a
     wrong interpretation), then present `rendered` as the view's `skill_md` directs. Do
     not re-format the rows. If `truncated` is true, say how many rows were shown of
     `rows_total`. Continue to Ground when the answer needs source text.
   - `needs_disambiguation`: show the `candidates` (name, class, `observation_count`),
     ask the user which one, then rerun with `--param <param>=@<_id>`.
   - `no_match`: tell the user nothing matched `input`. If `suggestions` is non-empty,
     offer them as "did you mean" (name, class); if the user picks one, rerun with
     `--param <param>=@<_id>`. Otherwise continue with Discover.

If no view fits, or the result is empty or does not answer the question, continue with
Store routing and then Discover as normal. Never invent a view name; only run names `orient --domain` listed.
