"""Route tests for the Lane B admin benchmark driver endpoints.

Monkeypatches the business-logic functions imported into benchmark_routes
(same style as test_webui_admin_api.py) so no real LLM/agent calls happen.
"""

import time

from fastapi.testclient import TestClient

from artmind.webui import benchmark_routes
from artmind.webui.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(admin_routes=True))


def test_benchmark_routes_absent_from_qa_app():
    client = TestClient(create_app())
    assert client.get("/api/benchmark/runs").status_code == 404
    assert client.post("/api/benchmark/preview", json={"path": "x"}).status_code == 404


def test_preview_rejects_missing_path(tmp_path):
    missing = tmp_path / "nope.md"
    response = _client().post("/api/benchmark/preview", json={"path": str(missing)})
    assert response.status_code == 400


def test_preview_parses_and_camelizes(monkeypatch, tmp_path):
    questions_file = tmp_path / "q.md"
    questions_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(
        benchmark_routes, "_parse_questions_file",
        lambda path: [
            {"seq": 1, "qid": "Q01", "title": "T", "question": "What?", "eval_comment": None}
        ],
    )
    response = _client().post("/api/benchmark/preview", json={"path": str(questions_file)})
    assert response.status_code == 200
    assert response.json() == {
        "questionCount": 1,
        "questions": [{"seq": 1, "qid": "Q01", "title": "T", "question": "What?", "evalComment": None}],
    }


def test_create_run_rejects_no_parsed_questions(monkeypatch, tmp_path):
    questions_file = tmp_path / "q.md"
    questions_file.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(benchmark_routes, "_parse_questions_file", lambda path: [])
    response = _client().post("/api/benchmark/runs", json={"path": str(questions_file)})
    assert response.status_code == 400


def test_create_run_launches_background_run(monkeypatch, tmp_path):
    questions_file = tmp_path / "q.md"
    questions_file.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        benchmark_routes, "_parse_questions_file",
        lambda path: [{"seq": 1, "qid": "Q01", "title": None, "question": "q?", "eval_comment": None}],
    )

    captured = {}

    def fake_create(source_path, questions, domain_hint=None, backend="claude-sdk", name=None):
        captured["source_path"] = source_path
        captured["questions"] = questions
        captured["domain_hint"] = domain_hint
        captured["backend"] = backend
        captured["name"] = name
        return "run-123"

    monkeypatch.setattr(benchmark_routes, "_create_benchmark_run", fake_create)

    ran = {"called_with": None}

    async def fake_run_benchmark(run_id):
        ran["called_with"] = run_id

    monkeypatch.setattr(benchmark_routes, "_run_benchmark", fake_run_benchmark)

    response = _client().post(
        "/api/benchmark/runs",
        json={"path": str(questions_file), "domainHint": "banking", "backend": "claude-sdk", "name": "run1"},
    )
    assert response.status_code == 200
    assert response.json() == {"runId": "run-123", "questionCount": 1}
    assert captured["domain_hint"] == "banking"
    assert captured["name"] == "run1"

    for _ in range(50):  # background task; give the event loop a moment to run it
        if ran["called_with"] is not None:
            break
        time.sleep(0.01)
    assert ran["called_with"] == "run-123"


def test_list_runs_camelizes(monkeypatch):
    monkeypatch.setattr(
        benchmark_routes, "_list_benchmark_runs",
        lambda status_filter=None: [{"run_id": "r1", "status": "queued", "question_count": 3}],
    )
    response = _client().get("/api/benchmark/runs")
    assert response.status_code == 200
    assert response.json() == [{"runId": "r1", "status": "queued", "questionCount": 3}]


def test_active_runs(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_fetch_active_benchmark_runs", lambda: [{"run_id": "r1"}])
    response = _client().get("/api/benchmark/runs/active")
    assert response.status_code == 200
    assert response.json() == [{"runId": "r1"}]


def test_completed_runs_passes_limit(monkeypatch):
    seen = {}

    def fake(limit=100):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(benchmark_routes, "_fetch_completed_benchmark_runs", fake)
    response = _client().get("/api/benchmark/runs/completed?limit=7")
    assert response.status_code == 200
    assert seen["limit"] == 7


def test_run_detail_found(monkeypatch):
    monkeypatch.setattr(
        benchmark_routes, "_get_benchmark_run",
        lambda run_id: {"run_id": run_id, "status": "processing"},
    )
    response = _client().get("/api/benchmark/runs/abc")
    assert response.status_code == 200
    assert response.json() == {"runId": "abc", "status": "processing"}


def test_run_detail_404(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_get_benchmark_run", lambda run_id: None)
    response = _client().get("/api/benchmark/runs/nope")
    assert response.status_code == 404


def test_question_detail_found(monkeypatch):
    monkeypatch.setattr(
        benchmark_routes, "_get_benchmark_question",
        lambda run_id, seq: {"seq": seq, "answer_text": "Paris"},
    )
    response = _client().get("/api/benchmark/runs/abc/questions/1")
    assert response.status_code == 200
    assert response.json() == {"seq": 1, "answerText": "Paris"}


def test_question_detail_404(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_get_benchmark_question", lambda run_id, seq: None)
    response = _client().get("/api/benchmark/runs/abc/questions/1")
    assert response.status_code == 404


def test_cancel_run_success(monkeypatch):
    monkeypatch.setattr(
        benchmark_routes, "_cancel_benchmark_run",
        lambda run_id: {"run_id": run_id, "cancelled": True, "status": "cancelled"},
    )
    response = _client().post("/api/benchmark/runs/abc/cancel")
    assert response.status_code == 200
    assert response.json() == {"runId": "abc", "cancelled": True, "status": "cancelled"}


def test_cancel_run_missing_returns_404(monkeypatch):
    def raise_not_found(run_id):
        raise ValueError(f"Benchmark run '{run_id}' not found")

    monkeypatch.setattr(benchmark_routes, "_cancel_benchmark_run", raise_not_found)
    response = _client().post("/api/benchmark/runs/nope/cancel")
    assert response.status_code == 404


def test_export_markdown(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_export_benchmark_markdown", lambda run_id: "# Report\n")
    response = _client().get("/api/benchmark/runs/abc/export?format=md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text == "# Report\n"


def test_export_markdown_404(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_export_benchmark_markdown", lambda run_id: None)
    response = _client().get("/api/benchmark/runs/nope/export?format=md")
    assert response.status_code == 404


def test_export_json(monkeypatch):
    monkeypatch.setattr(
        benchmark_routes, "_export_benchmark_json",
        lambda run_id: {"run_id": run_id, "questions": []},
    )
    response = _client().get("/api/benchmark/runs/abc/export?format=json")
    assert response.status_code == 200
    assert response.json() == {"runId": "abc", "questions": []}


def test_export_json_404(monkeypatch):
    monkeypatch.setattr(benchmark_routes, "_export_benchmark_json", lambda run_id: None)
    response = _client().get("/api/benchmark/runs/nope/export?format=json")
    assert response.status_code == 404


def test_export_rejects_bad_format():
    response = _client().get("/api/benchmark/runs/abc/export?format=csv")
    assert response.status_code == 400
