# Survey and topic plans
Read when a result carries `guide: survey`, or before a question that spans documents.

## Survey — questions that span documents

Some questions need coverage, not one passage: "what do the contracts say about
termination", "compare the approaches to X", "overview of Y", "which reports mention Z".
`vector-text` returns its top passages, which cluster in one or two documents. Use:

```bash
artmind query survey --shape brief --domain <d1> --domain <d2> --compact "<question>"
```

It ranks documents by their summaries and passages and returns, per document, its
`summary`, its best `passages` (with `id` and `chunk_id`) and its best `chunk_summary`;
`warnings` says when documents in scope have no summaries yet. `coverage` says how many
documents match the question's words in scope (`matching_documents`) and how many the
payload shows evidence for (`shown`).

Use `--shape brief` for these retrieval commands: rows carry title, date and status. A
survey lists every document: full passages for the strongest matches, a question-matched
`gist` for the next ones, and one line for the rest. `gist_source: passage` is evidence;
a summary gist is only a guide. Open a document with `artmind query survey --domain <d>
--shape brief --expand <doc_id> "<question>"` or `artmind query chunks --shape brief
--idList <chunk_id>`. A `truncated: true` result's `next` is `artmind query page <handle> --from N` (no `--domain`).

| The question asks for | Command |
|---|---|
| A fact (who, when, a figure, a quote) | the graph steps below, or `vector-text --topK 10` in a chunks-only domain |
| One document ("summarise X", "what does X say about Y") | `query graph pattern10 --documentName X`: its `summaries` holds the stored summary; read chunks only for detail |
| One entity broadly ("tell me about Acme") | `entity-context` (graph domains) |
| A topic across documents | a fitting topic plan (`query topics run`), else `query survey`, then `query chunks --shape brief --idList <chunk_id>` for the passages you cite |
| Patterns over time | insights |

Rules:
- A summary is LLM text, not evidence. Use summaries to choose documents and sketch an
  overview; quote facts from passages and cite their documents. For "summarise X" the
  stored summary may be the answer, labelled as artmind's summary of X, with X cited.
- One `query survey` call covers every domain in the set, as with `vector-text`.

### Topic plans

A topic plan is a reviewed retrieval program for an overview or trend question: its
subjects, its slices (per year or month, per child domain) and how many documents each
cell reads. Plans are built by the domain owner.

```bash
artmind query topics plan --list --domain <d> --compact    # slug, topic, subjects
artmind query topics run <slug> --shape brief --compact    # add --subject N or --cell <period|domain> for part
```

`orient --domain`'s `levels` block lists the domain's plans (slug, topic, subjects). One rule:

> A topic plan whose topic and subjects fit the question is a reviewed way to cover it:
> run `query topics run <slug>` (one subject or cell at a time if the question needs only
> part), and answer from those rows. When none fits and `survey`'s `coverage` shows the
> answer rests on a small share of `matching_documents` for an overview or trend question,
> answer from what you read, say how many more documents match, and end with one line: `Offer: a topic plan on
> <topic> (subjects: <s1>; <s2>; span <from>–<to>) would cover all <N> matching documents.` Never build it yourself.

- Each row is ordinary survey evidence: cite its passage and document as usual. A cell
  marked `partial` has more matching documents than it shows; say so.
- `topic build` / `refresh` cost LLM calls: the owner's (admin console, `artmind-build-topic`), never yours.
