# 02 — Trace evidence

One section per probe round, newest last. Each round: vault, window, backend/model if
known, headline numbers, observed failure patterns (anonymised — see the README's privacy
rule), and the scenario ids they feed in `04-scenarios.md`.

---

## Round 1 — 2026-10-19

**Vaults:** `brain01` (traces), `my_work` (structured store only; no traces, Neo4j down).
**Window:** brain01 admin-console turns 2026-10-12 → 2026-10-19, about 60 turns. Backend
mostly `acp`.
**Skill baseline:** commit `89da8fa`.

### brain01 — `artmind traces stats`

| Week | Turns | views_first | views_skipped | view_run_rate | views_listed_unused | fall_through | mean graph attempts before fall-through |
|---|---|---|---|---|---|---|---|
| 2026-10-12 | 40 | 0.85 | 0.15 | 0.25 | 13 | 0.10 | 7.5 |
| 2026-10-19 | 5 | 1.0 | 0.0 | 0.0 | 2 | 0.0 | — |

Answer basis (week of 10-12): graph 26, vector 4, mixed 5, none 5.
Two saved views existed in the domain: `trips_by_year` (params PERSON, LOCATION, year,
merge window) and `meetings_in_range`.

### Patterns observed

**P1. The same question routed five different ways.** "How many trips did PERSON make to
LOCATION_A [in YEAR]" was asked five times across sessions. A view (`trips_by_year`)
answers exactly this shape. Routes actually taken:

- `domains-overview → views list → views show ×2 → entity-resolve ×2 → views run` (right)
- `domains-overview → views list → entity-resolve ×2 → views run → entity-resolve → views run` (right, with a retry)
- `domains-overview → structural-metadata → entity-resolve → entity-context → chunks → pattern8` (views skipped)
- `domains-overview → entity-resolve → text2cypher → pattern1 → text2cypher ×2` (views skipped)
- a follow-up turn going straight to `text2cypher → entity-context`

The user had to intervene twice ("doesn't the skill say views first?", "why did you not use
the view"). → S1, S2.

**P2. Long tails when no view fits or the view returns empty.** Count-of-visits questions
for other PERSON/LOCATION pairs and years took 14, 21, 21, 23 and 28 calls. Typical
shape: `domains-overview → [views list] → structural-metadata/metadata → entity-resolve ×2–3
→ pattern7/pattern1 → text2cypher ×2–4 → vector-text ×2–4 → pattern10 ×N → Read note
files`. One investigative follow-up ran `pattern10` more than a dozen times, once per daily
note. → S3, S4.

**P3. A view run that returns empty sends the agent off-protocol.** When `trips_by_year`
returned no rows for a pair the user believed had trips, the agent fanned out across
`timeline`, `pattern10`, `vector-text` and note reads instead of reporting the empty view
result and offering one targeted check. → S4.

**P4. Follow-up turns skip the skill.** Many follow-ups don't reload the skill and call a
remembered command directly (`entity-history`, `pattern10`, `text2cypher`), never matching
the new question against the view catalogue. Views-first only holds when the skill is
freshly loaded. → S2.

**P5. History/provenance questions land on text2cypher.** "History of entity X",
"observations for document D", "what got created by the latest ingest": several turns used
`text2cypher` or `metadata`, even though `entity-history --name` and `document-history`
exist (and the skill warns against text2cypher for history). Some turns produced no
command at all. → S5.

**P6. Insights questions are well-routed.** Turns seeded by the console's "about the
insight …" prompt reliably ran `views list → insights list/show → entity-context`, about
5–9 calls. Detours: `pattern5` ×2 on same-as pairs, `query graph conflicts --help` (the
agent unsure of the option shape). → S6.

**P7. Operator turns mix skills.** Curate, update and same-as turns load other skills and
use `query` commands only to check results. They are outside artmind-query's routing, but
show `entity-resolve`/`pattern6` used as quick verification probes. Not a routing target;
noted so later rounds don't count them as misroutes.

### my_work — structured store (no traces)

- `db bridge`: one table (`TABLE_1`, domain `performance_management`, grain `instance`),
  four entity classes mapped (three unconfirmed, one confirmed), twelve `term` bridge
  columns, all unconfirmed. Several mapped columns are PERSON-valued (manager chains).
- Implication: every structured question depends on the agent reading `db bridge` and
  then the 1,500-word `structured-store.md` to classify it. No evidence yet of how agents
  actually route there. **Needs a round of Q&A in my_work** — see `06-open-questions.md`.

---

## Round 2 — 2026-10-20 (short note)

**Vault:** `banking_corpus` (new): eight sibling document domains `FAMILY.*` plus one literal
`FAMILY` domain holding five structured tables. No saved views in any domain.
**Window:** one Q&A turn (the vault's latest admin turn; the other recorded turn is an
operator prompt, out of scope per P7). **Backend/model:** `claude-sdk`, `claude-haiku-4-5`,
the first non-`acp` evidence (see `06-open-questions.md` Q1). **Skill baseline:** commit
`9304704`, `artmind_version` 0.10.0.

### Patterns observed

**P8. The Route step is skipped when the question contains a noun phrase that sounds like a
domain.** Question shape: "list the main DOC_KIND present in the CORPUS_NAME corpus". The
agent took CORPUS_NAME (the vault's folder name) as the domain, reasoned "I have a specific
domain, so I can use it directly", and loaded the skill with that domain. `domains-overview`
was never run, although the skill requires it unless the user named an exact domain. The
route taken was `views list → entity-listing → structural-metadata → db bridge`, every call
against the invented domain.

**P9. The CLI answers an unknown domain with an empty success.** All four calls in P8
exited 0 with `rows: []`, `total_entities: 0`, `tables: []`. Nothing said the domain does
not exist, so the agent read "no rows" as "no data" and told the user the corpus had no
ingested data and to ingest documents. The data was there: the real DOC_KIND domain held 9
documents and about 1.5k entities, and `domains-overview` lists them in one call. The
answer was confidently wrong, `answer_basis: none`, and cost 7 turns, 28 s, about $0.08
(221k input tokens). The skill's "thin results → re-run with sibling domains from Route"
rule could not fire, since Route never ran.

### Scenario ids

S15 (a name that is an area, not a domain; ◐), new S18 (inventory: what documents of kind
K exist).
