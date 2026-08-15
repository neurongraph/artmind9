---
title: artmind_canvas — dev vault
project: artmind_canvas
area: engineering
tags: [phase-0, skeleton]
---

# Phase 0 walking skeleton

This file lives in the **dev vault** — a stand-in for the user's real Vault
(`ARTMIND_VAULT_DIR`) so the Phase-0 skeleton has something real to render.

When you type `/render-test README.md` in the Chat dock, the backend streams a
canned turn that ends with a **`render`** event carrying a `document` Card spec.
The frontend spawns a Card on the Canvas, which fetches this file through
`GET /api/vault/file?path=README.md` and shows it.

That single round trip proves the whole architecture:

- the React + Vite frontend and React Flow canvas,
- the dedicated canvas backend reusing artmind's `ClaudeSDKBackend`,
- the SSE chat contract, and
- the new `render` event → Card seam.

Everything else in the plan is additive on top of this skeleton.
