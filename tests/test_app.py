from __future__ import annotations

import app as app_module
from fastapi.testclient import TestClient

from app import app
from src.services import ReviewOutcome


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_review_github_endpoint(monkeypatch):
    client = TestClient(app)

    async def fake_review(*args, **kwargs):
        return ReviewOutcome(
            result={
                "summary": "Reviewed 1 file(s).",
                "files": [],
                "findings_total": 0,
                "per_file_diffs": {},
            }
        )

    monkeypatch.setattr(app_module.review_service, "review_github", fake_review)

    resp = client.post(
        "/review/github",
        json={"owner": "acme", "repo": "demo", "pr_number": 1, "max_files": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"].startswith("Reviewed")


def test_github_webhook_triggers_review(monkeypatch):
    client = TestClient(app)

    async def fake_process(*args, **kwargs):
        return ReviewOutcome(
            result={
                "summary": "Reviewed",
                "files": [],
                "findings_total": 0,
                "per_file_diffs": {},
            }
        )

    monkeypatch.setattr(app_module, "verify_github_signature", lambda raw, sig: True)
    monkeypatch.setattr(app_module.review_service, "process_pull_request_event", fake_process)

    payload = {
        "action": "opened",
        "number": 5,
        "repository": {"name": "demo", "owner": {"login": "acme"}},
        "pull_request": {"head": {"sha": "headsha"}},
    }

    resp = client.post(
        "/github/webhook",
        json=payload,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
