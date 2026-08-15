# Live skill authoring: reconnect the agent session to load new skills

Skills are read only at agent-session start — there is no hot-reload (this is how
artmind's skill seeding works: `SKILL.md` is picked up when the agent process starts,
its `cwd` scoped to a skills directory). So authoring a skill mid-session and using it
immediately requires refreshing the agent session. Chat is backed by *both* harnesses —
the Claude Agent SDK and opencode ACP (Q18) — and their session-resume stories differ.

We decided: the authoring flow is **write `SKILL.md` to a canvas-owned skills directory
→ refresh the agent session so the new skill is discoverable**. On the Claude SDK path,
reconnect with `resume=<session_id>` (SDK fork/resume) so the conversation context
survives the refresh. On the ACP path, if resume is unavailable, **restarting the
session without preserving context is acceptable** (Q18) — the user has explicitly
okayed losing the thread there.

Why: the user wants author → save → use-now without losing the conversation when that's
cheap. SDK resume makes context preservation nearly free; ACP resume is unverified, and
a clean restart is an acceptable fallback rather than a blocker.

Consequences:
- The canvas owns a skills directory it writes to. It must **not** be `~/.artmind/.claude/
  skills/` (which `artmind init` overwrites) nor the checkout symlinks — it is
  canvas-managed, and the agent's skill-discovery path must include it.
- Authoring a `SKILL.md` and pointing the agent at it is client/backend behaviour — it
  stays within the pure-client boundary (ADR 0003); no artmind pipeline change.
- Confidence: SDK `resume` exists (high); ACP resume is to be verified at build time.
  No hot-reload is assumed on either path.
