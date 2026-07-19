# Banking Corpus Q&A Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the starter banking questions with a commented, evidence-grounded benchmark that exposes Artmind's graph-specific Q&A strengths.

**Architecture:** The benchmark is a single Markdown catalogue in `banking_document_corpus/questions.md`. Its introduction defines the evaluation convention; themed sections then contain realistic prompts and compact evaluator comments that name the corpus evidence and reasoning behaviour required.

**Tech Stack:** Markdown, repository corpus documents, `rg`, `git`.

---

## File structure

- Modify: `banking_document_corpus/questions.md` — self-contained benchmark and evaluator guidance.
- Reference: `banking_document_corpus/policies/policy_complaints.md` and `policy_complaints_v3.md` — a formal supersession and historical inconsistency.
- Reference: `banking_document_corpus/reference/interest_rate_schedule_2026.md` — dated current and historical rate evidence.
- Reference: `banking_document_corpus/regulations/regulatory_circulars_2026.md` and `governance/board_risk_committee_minutes_q1_2026.md` — changing regulatory implementation status.
- Reference: `banking_document_corpus/{policies,sop_procedures,guides,risk_compliance,governance,reference,templates}/` — operational and cross-cutting evidence.

### Task 1: Map evidence to benchmark categories

**Files:**
- Reference: `banking_document_corpus/**/*.md`

- [ ] **Step 1: Inventory the available temporal, supersession, conflict, and cross-cutting evidence**

Run:

```bash
rg -n -i 'supersedes|effective|history|status|deadline|exception|remediation|vulnerable' \
  banking_document_corpus --glob '*.md'
```

Expected: evidence supporting every benchmark category, including complaint policy v2/v3, interest-rate history, regulatory circulars, governance minutes, and operational SOPs.

- [ ] **Step 2: Select questions with traceable evidence**

Create a compact working mapping before editing: each prompt must have at least one named document, while graph-specific prompts must require two or more documents or a version/date relationship. Exclude questions that require facts not represented in the corpus.

- [ ] **Step 3: Verify the selected evidence paths exist**

Run:

```bash
rg --files banking_document_corpus | sort
```

Expected: every file cited in an evaluator comment appears in the output.

### Task 2: Create the commented question catalogue

**Files:**
- Modify: `banking_document_corpus/questions.md`

- [ ] **Step 1: Replace the starter list with the benchmark introduction and usage convention**

Write an introduction explaining that prompts are submitted verbatim and that each `Evaluation comment` names the expected evidence and reasoning behaviour. State that a correct answer should cite sources, respect effective dates and supersession, and disclose unresolved disagreement.

- [ ] **Step 2: Add baseline operational and product questions**

Add realistic questions covering customer vulnerability during address changes, standing orders/direct debits, SmartSaver features and rates, complaints, and fraud. Each entry must use this exact pattern:

```markdown
### Q01 — <label>

**Question:** <natural-language prompt>

**Evaluation comment:** <capability and source evidence>
```

- [ ] **Step 3: Add temporal, supersession, and conflict questions**

Include prompts that require selection of the controlling complaint-policy version before and after 2026-06-01, historical versus current rate interpretation, and disclosure of the former policy's internally inconsistent approval thresholds. Comments must name dates and source files.

- [ ] **Step 4: Add cross-cutting investigation and governance questions**

Include scenarios that link regulations to procedures and training, privacy rights to AML retention, operational incidents to escalation and risk appetite, audit findings to remediation, and system responsibilities to customer impact. Comments must identify the evidence chain.

- [ ] **Step 5: Add a corpus-enhancement appendix**

Append a short, clearly separated recommendation list: dated SOP change pack, independent monthly rate documents, case-event timeline, unresolved factual discrepancy, regulatory implementation artefacts, authority register, retention/erasure case, and post-deadline open-banking outcome. Label these as optional future corpus additions, not assumptions available to the current benchmark.

### Task 3: Validate the benchmark

**Files:**
- Test: `banking_document_corpus/questions.md`

- [ ] **Step 1: Verify question and comment coverage**

Run:

```bash
rg -n '^### Q|^\*\*Evaluation comment:\*\*' banking_document_corpus/questions.md
```

Expected: every numbered question is immediately followed by one evaluation comment; the number of question headings and comments is identical.

- [ ] **Step 2: Verify Markdown and repository references**

Run:

```bash
git diff --check
rg -n 'TBD|TODO|\[insert|\[source\]' banking_document_corpus/questions.md
```

Expected: no whitespace errors and no placeholder matches.

- [ ] **Step 3: Review the final diff and commit**

Run:

```bash
git diff -- banking_document_corpus/questions.md
git add banking_document_corpus/questions.md
git commit -m "docs: add banking Q&A benchmark"
```

Expected: the diff contains only the benchmark catalogue and optional corpus-enhancement appendix; Git records one documentation commit.

## Self-review

- Spec coverage: Tasks 1–3 cover each required benchmark category, the per-question comment format, traceability, and a usable corpus-gap appendix.
- Placeholder scan: no placeholder language appears in task instructions or expected output.
- Consistency: every planned question uses one Markdown entry format, and only existing corpus files are permitted in evaluator comments.
