# Plan labels: a cheat sheet

How to confirm the labels `artmind plan replay` grades the planner against. The architecture
behind them is [`question-routing.md`](question-routing.md). Labels hold real questions, so
they live only in each vault (`<vault>/benchmarking/plan_labels.yaml`) and are committed, if
at all, to the vault's own repository. Examples here are invented.

## What a label is for

A label records what the planner *should* have done with one question. Replay re-plans the
question and grades the plan against it. A label drafted by the planner and left unchanged
only checks the planner against itself, so every draft needs a human pass.

## The fields

| Field | Put | Ask yourself |
|---|---|---|
| `id`, `question`, `source` | leave as drafted (`source: trace:<id>` names the turn it came from) | |
| `intent` | `retrieve` for a question about the notes, else one of the seven below | Is it a question at all? |
| `domains` | every domain that holds the answer, or the domain the question names (optional for a non-retrieval intent). No domain named → list every domain that holds the answer; in a vault of chunks-only domains, all of them. A family parent stands for all its children | Is the whole answer there? |
| `route` | the question shape, from the route table (optional for a non-retrieval intent) | Would I answer it this way? |
| `view`, `params` | only with `route: view`: the saved view and its parameter values | Right view, right person or value? |
| `expect` | `high` when the right plan is obvious enough to run unjudged; `low` when the question is ambiguous or needs judgement | Should the planner be sure? |
| `slots` (optional) | expected slot values, e.g. `search_term: "harbour visits"`, `narrator: "<name>"`, `date_window: "2025-01-01..2026-01-01"` | |
| `program_kind` (optional) | recorded only, not graded | |
| `expected_documents` (optional) | vault-relative paths (`folder/file.md`) of the documents that hold the answer; used only by `plan replay --reach`, matched to step documents by path, then file name, case-insensitive | Which documents must the plan's steps surface? |
| `follows` (optional) | a label id: the label's question is a reply to that label's, so replay plans it with the previous turn; `none` plans it alone. Left out, a `trace:<id>` label follows the turn before it in its conversation (the label made from that turn, else the recorded turn) | Does the question only make sense after another? |
| `notes` | delete the DRAFT line; keep a note if it helps | |

A reply such as "yes please" or "run as is" is only a question in the context of the turn before it. Replay
plans a followed label first and hands its own plan (and, for a trace label, that turn's recorded answer) to the
follower as the previous turn, as the live agent does. Chains work (`L003` follows `L002` follows `L001`); cycles
and unknown ids are rejected by `artmind plan labels validate`.

## How a label is graded

**Retrieval labels** (`retrieve`, `follow_up_why`, or no intent): domains, route, view and
view parameters are compared; a plan that came back with a non-retrieval intent is wrong.
Domains match when the plan searches every labelled domain and nothing beside them: a family
parent matches a label listing its children, and a plan that searches only some of the labelled
domains is wrong.

Then the confidence rule:

| Plan | Label `expect` | Outcome |
|---|---|---|
| high, everything matches | high | correct |
| high, something differs | high | wrong |
| high | low | wrong (overconfident) |
| low | high | abstained (not wrong, a lost chance) |
| low | low | correct abstain |
| a non-retrieval intent | any | wrong (a lookup question planned as none) |

**Non-retrieval labels** (`follow_up_format`, `curate`, `update`, `meta`, `status`, `ops`)
are graded on one thing: the plan must not be a confident lookup. The agent decides for itself
whether a turn needs a lookup and handles these turns from the conversation, so the only harm is
a confident search planned for a turn that needs none.

| Plan | Outcome |
|---|---|
| any non-retrieval intent (right or not), high | correct |
| any non-retrieval intent, low | abstained, or correct abstain with `expect: low` |
| a lookup, low | abstained, or correct abstain with `expect: low` (harmless: the agent decides) |
| a lookup, high | wrong |

Which non-retrieval intent the plan chose is still reported (`intent_ok`, `nonretrieval.intent_wrong`).

**Planned routes** (`episode`): a label naming a route that is designed but not built is
`planned`: replay records what the planner chose and grades nothing else, and the label is
left out of the wrong rate. A plan with a non-retrieval intent is still wrong.

**Gate** (`--repeat 4`): no wrong view, under 10 % wrong overall, no errors, no plan that
flips between repeats, and no non-retrieval label planned as a confident lookup. When unsure,
`expect: low` cannot count against you.

**Where to spend labelling time.** On questions that need a lookup, follow-ups included ("what
about X", "check again for December"): those are the turns the planner serves. A few
non-retrieval labels are enough to keep the safety net honest; their exact intent matters less.

## Intents

| Intent | The message | Invented example |
|---|---|---|
| `retrieve` | a question about the notes | "where did we stay on the coast trip?" |
| `follow_up_format` | reshape the last answer | "make that a table" |
| `follow_up_why` | challenge the last answer | "why did you miss the second visit?" |
| `curate` | a correction to the graph | "those two are the same person" |
| `update` | a fact to store | "remember that the meeting moved" |
| `meta` | about the assistant | "which model are you?" |
| `status` | progress of a job | "is it done?" |
| `ops` | a pipeline step | "re-extract that note" |

A bare "yes please" or "go ahead" is `retrieve` in a label: replay has no previous turn.

## Routes

| Route | The question asks | Invented example |
|---|---|---|
| `existence` | did it happen, or is it mentioned at all | "did I visit the lake again?" |
| `enumeration` | a complete list of a kind | "list all the trips" |
| `aggregate` | a count, total, average, frequency | "how many times did we eat out?" |
| `superlative` | the most, least, top, favourite | "my favourite café" |
| `entity_profile` | who or what one named thing is | "tell me about Ann" |
| `entity_history` | how what is known about it changed | "how has the project changed?" |
| `time_history` | which version of a document applied when | "what did the policy say in March?" |
| `conflicts` | whether sources disagree | "do the notes contradict each other?" |
| `hierarchy` | who or what is above or below | "who reports to Ann?" |
| `path` | how two named things are connected | "how are Ann and Ben related?" |
| `document` | what one named document says | "summarise the lease" |
| `inventory` | which documents of a kind exist | "what policies do we have?" |
| `structured` | numbers in tables | "the balance per account" |
| `insights` | what changed, who matters, who drifted | "who have I lost touch with?" |
| `filing` | notes under a tag, project or area | "my notes tagged garden" |
| `survey` | what several documents say on one topic | "what do the contracts say about notice?" |
| `search` | any other factual question, or how to do something | everything else; "what are the steps to renew?" |
| `view` | a saved view answers it exactly | with `view:` and `params:` |
| `episode` *(planned)* | when something happened, how long it lasted, what surrounded it | "when did Ann go into hospital, and for how many days?" |

`search` and `survey` run the same two commands, so replay grades them as one route: a label saying either is matched by a plan saying either (`route_exact` in the summary still counts exact matches). Label the one that reads best.

Use `episode` when the answer is a span or the context of one happening (start, end, how
long, what came before or after), not a single fact. Use `existence` for "did it happen at
all", and `search` when one passage answers it. Give it `expect: low` until the route is built.

A question that names a class of the domain and asks which exist ("are there any IDEAs?",
"list my PLANs") is `enumeration`, not `search`: the planner lists that class. The same
question over a word that is not a class ("are there any recipes?") is `search`.

Chunks-only domains have no graph: their labels can only be `survey` (one topic across
documents) or `search` (a specific fact).

## Workflow (inside each vault)

```bash
artmind plan labels validate --file benchmarking/plan_labels.draft.yaml
mv benchmarking/plan_labels.draft.yaml benchmarking/plan_labels.yaml
ARTMIND_NO_PROXY=1 artmind plan replay --repeat 4
```

The summary prints label ids and outcomes, never question text; per-label detail (which
field mismatched) goes to `<vault>/.artmind/logs/plan-replay-*.json`.
