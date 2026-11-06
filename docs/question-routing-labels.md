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
| `domains` | the domain that holds the answer (optional for a non-retrieval intent) | Is the answer there? |
| `route` | the question shape, from the route table (optional for a non-retrieval intent) | Would I answer it this way? |
| `view`, `params` | only with `route: view`: the saved view and its parameter values | Right view, right person or value? |
| `expect` | `high` when the right plan is obvious enough to run unjudged; `low` when the question is ambiguous or needs judgement | Should the planner be sure? |
| `slots` (optional) | expected slot values, e.g. `search_term: "harbour visits"`, `narrator: "<name>"`, `date_window: "2025-01-01..2026-01-01"` | |
| `program_kind` (optional) | recorded only, not graded | |
| `notes` | delete the DRAFT line; keep a note if it helps | |

## How a label is graded

**Retrieval labels** (`retrieve`, `follow_up_why`, or no intent): domains, route, view and
view parameters are compared; a plan that came back with a non-retrieval intent is wrong.

**Non-retrieval labels** (`follow_up_format`, `curate`, `update`, `meta`, `status`, `ops`):
only the intent is compared; route and domains are ignored.

Then the confidence rule:

| Plan | Label `expect` | Outcome |
|---|---|---|
| high, everything matches | high | correct |
| high, something differs | high | wrong |
| high | low | wrong (overconfident) |
| low, intent right | high | abstained (not wrong, a lost chance) |
| low, intent right | low | correct abstain |
| any, intent wrong | any | wrong |

**Planned routes** (`episode`): a label naming a route that is designed but not built is
`planned`: replay records what the planner chose and grades nothing else, and the label is
left out of the wrong rate. A plan with a non-retrieval intent is still wrong.

**Gate** (`--repeat 4`): no wrong view, under 10 % wrong overall, no errors, every
non-retrieval intent right. When unsure, `expect: low` cannot count against you.

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
| `procedure` | how to do something | "what are the steps to renew?" |
| `structured` | numbers in tables | "the balance per account" |
| `insights` | what changed, who matters, who drifted | "who have I lost touch with?" |
| `filing` | notes under a tag, project or area | "my notes tagged garden" |
| `survey` | what several documents say on one topic | "what do the contracts say about notice?" |
| `search` | any other factual question | everything else |
| `view` | a saved view answers it exactly | with `view:` and `params:` |
| `episode` *(planned)* | when something happened, how long it lasted, what surrounded it | "when did Ann go into hospital, and for how many days?" |

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
