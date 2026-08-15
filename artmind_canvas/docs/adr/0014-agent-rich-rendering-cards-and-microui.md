# Agent rich rendering: declarative typed Cards + sandboxed micro-UI, via a `render` event

The existing agent contract is 7 string-only, 600-char-clipped event types; agent output
is either sanitized markdown (raw HTML stripped) or plain-text tool traces. There is no
way for the agent to render an interactive widget. Because artmind_canvas has its **own
dedicated backend** (ADR 0003), it is not bound by that clipped contract and can define a
richer one — which both skill results (ADR 0013) and the micro-UI Card (ADR 0007's card
taxonomy) need.

We decided (Q26): a **two-tier** rendering model.
- **Tier (b) — declarative typed Cards.** The first-class Card types render from
  declarative specs. Each Card type defines a **Card contract**: the schema of the
  `props` it accepts (e.g. `graph-view` ← filter spec; `document` ← `{vaultPath,
  blockId}`; `provenance` ← block refs; `skill` ← `{skillName, inputs}`). The contract is
  owned client-side by the Card component but **published so the agent/backend emit
  conforming payloads**. Trusted, consistent, styleable.
- **Tier (c) — sandboxed micro-UI.** The `micro-UI` Card is a **sandboxed iframe**
  (`srcdoc` + `sandbox`) for arbitrary interactive agent-authored HTML/JS, isolated from
  the app DOM and data. Its only "contract" is "sandboxed HTML" — deliberately
  schema-free.

Mechanism: a new, **unclipped `render` event** carries either `{cardType, props}`
(declarative) or `{html}` (iframe), emitted from both harness mappers (which funnel
through one shared point, so a single new type reaches both the Claude SDK and ACP paths).

Why: declarative specs keep the known Cards safe/consistent; a sandboxed iframe gives the
escape hatch genuinely arbitrary interactivity without exposing the app; one new event
type covers both.

Consequences:
- A **Card contract** must be defined per first-class Card type (client-owned, shared to
  the agent).
- A rich render sink is needed, separate from — and not routed through — the existing
  DOMPurify markdown path (which would strip it); the iframe tier needs a strict sandbox.
- The `render` event lives in the **canvas's own backend contract** (a superset),
  leaving artmind's shared `webui/backends` untouched. This is agent-harness plumbing
  (ADR 0003 territory), distinct from the ingestion-pipeline boundary.
