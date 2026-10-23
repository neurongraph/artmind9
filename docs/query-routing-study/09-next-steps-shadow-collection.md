# 09 — Next steps: shadow-plan collection, then finalise `query plan`

Written 2026-10-23, at the end of the session that added the OpenRouter decision backend
(merge `547cabf`). The pick-up point for the next session on `query plan`. Phase 2 of
`08-fix-and-redesign-plan.md` §4 (shadow mode) is where we are.

## 1. State at hand-over

- `artmind query plan` v1 is on master, with shadow mode on: every admin-console and
  benchmark turn records a `plan_shadow` event beside the agent's trace.
- Decision model: `ARTMIND_DECISION_PROVIDER=openrouter`, `ARTMIND_DECISION_MODEL=typesafe/jev-1.14`
  in `~/.artmind/config.env` (machine-wide). OpenRouter passes Jev's decisions API through
  (`/api/alpha/decisions`, per-option probabilities); every call asks for
  `data_collection: deny, zdr: true`. No start-up warm-up for it. Local Ollama settings are
  commented out in the same file (to go back: drop the PROVIDER line, uncomment the URL,
  model `nimble` or `tev1`). A backup of the previous file is `~/.artmind/config.env.bak-20261023`.
- A vault's own `.artmind/config.env` overrides the machine one (`paths.py` loads the vault
  file first with `override=False`), so a vault can keep a different decision provider.
- Daemons (`serve`, both `admin-ui`) were left running with the old settings; they read
  `config.env` only at start and must be restarted from inside each vault.

## 2. Decision model comparison on Q26 (banking_corpus, admin console, 2026-10-23)

Same question three times; only the shadow plan's decision model changed.

| | jev-1.14, OpenRouter (`d127eacc`) | tev1, local (`f6bedf8c`) | nimble, local (`87e3e8d4`) |
|---|---|---|---|
| Decision latency | 1.1 s | 6.8 s | 60 s timeout → lexical |
| Domain | `banking.risk_governance` 0.93 | split (0.32 / 0.14) → all 9 domains | lexical tie 0.31 / 0.31 |
| Route | `search` 0.59 | `search` 0.69 | `search` 0.50 |
| Plan confidence | low 0.59 (weakest: route) | low 0.0 (weakest: domain) | low 0.31 |
| Entity candidates | from the chosen domain (bank, KYC policy/risk) | scattered over SOPs and letters | communications only |
| Admin turn duration | 43.6 s | 47.7 s | 205 s |

- jev chose the domain where the agent's productive call landed (`pattern10` on
  `enhanced_kyc_implementation_register.md`); tev1 could not.
- jev's route confidence (0.59) is now the only thing below the 0.7 threshold.
- No plan surfaced the register document: the planner offers a document only when the
  question names one. Gap to look at (document candidates from entity/chunk hits?).
- The agent's tool calls, final text and token counts were byte-identical across the three
  runs. Check whether the console replays/caches agent runs before relying on repeats.
- One question only: evidence that jev routes domains well, not that it is better overall.

## 3. Collection phase (user, before the next session)

Target: about 50 admin-console Q&A turns across `banking_corpus` and `brain01`.

1. **Decide brain01's decision provider first.** With the machine config, every brain01
   admin question and its candidate entity names go to OpenRouter/TypeSafe via shadow
   plans. Either keep brain01 local by adding to its `.artmind/config.env`:
   `ARTMIND_DECISION_PROVIDER=ollama`, `ARTMIND_DECISION_URL=http://localhost:11434/v1/systemone`,
   `ARTMIND_DECISION_MODEL=tev1` (slower, weaker domain choice, nothing leaves the machine),
   or accept the exposure so both vaults compare on jev. (Or `ARTMIND_PLAN_SHADOW=0` there.)
2. Restart `serve` / `admin-ui` from inside each vault so they pick up the settings.
3. **Rate as you go** (console thumbs, or `artmind traces feedback <id> good|partial|wrong`).
   Unrated traces show behaviour but not correctness, and triage ranks by rating.
4. **Vary question shapes deliberately** so every route is exercised: counts/aggregates,
   entity profile, change over time / history, a named document, cross-domain comparison,
   enumeration/superlative, store (table) questions, follow-ups, first-person (brain01).
5. **Note the known-right answer** (domain, route, entity, view) where you know it; these
   become `benchmarking/plan_labels.yaml` entries in each vault (private, never committed here).

## 4. Next session: analysis, then decisions

1. `artmind traces stats` and `artmind traces triage` per vault: plan-vs-agent
   disagreement (`plan_disagreed_high`), fallback rate and reasons, `views_skipped` /
   `views_late`, latency.
2. Probe per `05-probing.md`; append a round to `02-trace-evidence.md`, update
   `04-scenarios.md` and `06-open-questions.md`. Anonymise brain01 (README privacy rule).
3. Build/extend `plan_labels.yaml`; run `artmind plan replay --classifier both --repeat 4`
   for a jev-vs-lexical score against the gate.
4. Decide, with that evidence:
   - the confidence threshold (and whether route confidence should weigh less than domain);
   - `--execute` beyond a strong view match, or plan-only;
   - per-domain `domains/plan/<domain>.yaml` (narrator, threshold, classifier);
   - document candidates in the plan (the Q26 gap);
   - default decision provider/model, and whether hosted is acceptable for private vaults.
5. Then phase 3 of `08` §4: wire the plan into `artmind-query`, shrink the skill, and the
   CLI/skill fixes the review dossiers (`artmind review`) on wrong/partial traces point to.
