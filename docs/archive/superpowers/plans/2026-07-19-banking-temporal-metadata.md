# Banking Temporal Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make banking schema and knowledge-source temporal metadata complete and consistently extractable.

**Architecture:** Add a shared temporal contract to all banking schemas, create and map a cases schema, then mechanically add standard metadata fields to every in-scope corpus source document without changing substantive body content.

**Tech Stack:** YAML, Markdown, `rg`, `git`.

---

### Task 1: Add temporal schema contracts

**Files:**
- Modify: `artmind/domains/schemas/banking_{communications,organization,products,risk_governance}_schema.yaml`
- Create: `artmind/domains/schemas/banking_cases_schema.yaml`
- Modify: `banking_document_corpus/schema_mapping.md`

- [ ] Add `temporal.document.valid_from`, `version`, entity anchors, and `relative_anchor` appropriate to each schema; create the cases entity classes and map `cases/`.
- [ ] Validate YAML with `uv run python -c "import yaml; [yaml.safe_load(open(p)) for p in __import__('glob').glob('artmind/domains/schemas/banking*_schema.yaml')]"`.

### Task 2: Standardise corpus source metadata

**Files:**
- Modify: all in-scope `banking_document_corpus/**/*.md` source documents, excluding index, questions, and schema mapping.

- [ ] Add missing `Version`, `Status`, canonical date, `Supersedes`, and `Superseded By` fields to each metadata block; use `None` where no relationship exists and preserve explicit historical links.
- [ ] Add a metadata block to Board Risk Committee minutes, retaining the existing meeting date.

### Task 3: Audit and commit

**Files:**
- Test: schemas and corpus metadata.

- [ ] Run an audit that reports any in-scope Markdown file missing one required field, then run `git diff --check`.
- [ ] Review the diff and commit only schema, mapping, corpus metadata, and this plan changes.
