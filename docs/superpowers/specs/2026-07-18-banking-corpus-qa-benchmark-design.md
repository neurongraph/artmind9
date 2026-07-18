# Banking Corpus Q&A Benchmark Design

## Goal

Expand `banking_document_corpus/questions.md` into a practical benchmark that
compares Artmind with vector-only retrieval, especially where graph structure,
document authority, and time matter.

## Scope

The benchmark will retain the existing useful frontline intent while replacing
the unstructured list with themed, numbered questions. Every question will have
a concise comment that states the capability being tested and the evidence a
good answer must reconcile. Comments are evaluation guidance, not model answers.

## Question groups

1. **Baseline operational and product retrieval** — realistic questions whose
   answer is primarily in one or two related documents. These establish that the
   corpus remains useful for ordinary service and operations work.
2. **Temporal authority and supersession** — dated questions that require the
   system to select the correct policy/rate/procedure for a stated point in time
   and identify a formally superseded version where relevant.
3. **Conflicting claims and facts** — questions that require a response to
   expose disagreement, attribute it to its sources, and apply the document
   authority or effective-date rule rather than flattening it into one answer.
4. **Cross-cutting investigation and action** — scenarios connecting customer
   treatment, controls, SOPs, regulations, systems, escalation, risk, and
   governance across document families.

## Format

Each entry will contain:

```markdown
### Q01 — Short category label

**Question:** Natural-language question to submit to Artmind.

**Evaluation comment:** What Artmind should demonstrate, including the
relevant documents, dates, entities, or relationships. It will not prescribe
wording or disclose a full answer.
```

## Quality rules

- Use wording that a frontline colleague, compliance analyst, or manager might
  genuinely ask.
- Include explicit dates only when temporal selection is the intended test.
- Name expected documents in comments so a human evaluator can diagnose a
  retrieval, graph-traversal, or reasoning failure.
- Treat the active, higher-authority, or newer source as controlling only when
  the corpus explicitly establishes that relationship; otherwise require the
  answer to flag the discrepancy.
- Cover the complaint-policy v2/v3 supersession, dated interest-rate history,
  regulation-to-procedure links, and governance/audit/incident relationships.

## Acceptance criteria

- The completed catalogue is self-contained in `questions.md` and has a clear
  introduction explaining how to use it.
- Every question includes an evaluation comment.
- The suite contains both ordinary and graph-specific tests, with the latter
  prominently covering temporality, supersession, conflicts, and cross-cutting
  reasoning.
- All prompts and expectations are traceable to documents present in the
  corpus.
