# Global Temporal Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Apply deterministic temporal defaults only for schemas that opt in.

**Architecture:** Extend the temporal schema parser and document-date lifting logic; add defaults to temporal schemas and focused unit tests.

**Tech Stack:** Python, YAML, pytest.

---

### Task 1: Parse schema defaults

- [ ] Extend `artmind/temporal.py` so `lift_document_dates` accepts `defaults` and uses UTC ingestion date only after mapped source dates and frontmatter are absent.
- [ ] Preserve null `valid_to` and `superseded_by`; attach `time_source` and `valid_from_inferred` only for defaults.

### Task 2: Configure and test

- [ ] Add default blocks to schemas with `temporal:`.
- [ ] Add tests in `test/test_temporal.py` for header priority, default provenance, null open-ended validity, and schemas without temporal configuration.
- [ ] Run `uv run pytest test/test_temporal.py -v` and commit intended changes.
