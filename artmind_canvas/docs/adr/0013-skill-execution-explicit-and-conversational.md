# Skill execution: explicit invocation and conversational, rendered into a skill Card

Today skills are purely agent-decided — there is no direct-invoke surface, and the
existing UIs' "suggestion chips" only pre-fill the prompt box; the agent still chooses
whether to read a `SKILL.md`. The founding vision is "execute repeatable actions via
skills I author on the fly," which wants a real trigger, not only prose.

We decided (Q25): support **both explicit invocation and conversational use**, with the
`skill` Card as the surface.
- **Invocation**: an explicit path (a skill picker / slash-command that runs a named
  skill directly, sourced from the skill catalog) *and* the agent invoking skills
  mid-conversation.
- **Parameters**: extend `SKILL.md` frontmatter with an optional `inputs` spec; the skill
  Card renders those as a form. Skills with no declared inputs fall back to
  free-text / agent-driven invocation.
- **Results**: a skill's output travels on the new `render` event (ADR 0014) into a Card —
  a first-class Card type when the result fits one, otherwise the micro-UI escape hatch.

Why: repeatable actions deserve a one-click, parameterized trigger; conversational
invocation stays for fluid, exploratory use. A single skill can serve both.

Consequences:
- Needs an explicit invocation surface plus a skill catalog as its data source (the
  existing `help.py` catalog is the natural model).
- The `SKILL.md` format gains an optional `inputs` spec; the live-authoring flow
  (ADR 0007) writes it.
- Skill output depends on the `render` event and the per-type Card contracts (ADR 0014).
