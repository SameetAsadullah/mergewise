from __future__ import annotations

from fastapi.testclient import TestClient

from app import app


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_review_github_endpoint(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("app.ENABLE_CONTEXT_INDEXING", False)
    monkeypatch.setattr(
        "app.get_pr_details",
        lambda owner, repo, number: {
            "title": "Test PR",
            "diff": "diff --git a/file b/file\n",
            "base_sha": None,
        },
    )

    async def fake_review(*args, **kwargs):
        return {
            "summary": "Reviewed 1 file(s).",
            "files": [],
            "findings_total": 0,
            "per_file_diffs": {},
        }

    monkeypatch.setattr("app.review_pr_async", fake_review)
    monkeypatch.setattr("app.create_or_update_check_run", lambda *args, **kwargs: None)

    resp = client.post(
        "/review/github",
        json={"owner": "acme", "repo": "demo", "pr_number": 1, "max_files": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"].startswith("Reviewed")


def test_github_webhook_triggers_review(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr("app.ENABLE_CONTEXT_INDEXING", False)
    monkeypatch.setattr(
        "app.get_pr_details",
        lambda owner, repo, number: {
            "title": "Test PR",
            "diff": "diff --git a/file b/file\n",
            "base_sha": None,
        },
    )
    async def fake_review(*args, **kwargs):
        return {
            "summary": "Reviewed",
            "files": [],
            "findings_total": 0,
            "per_file_diffs": {},
        }

    monkeypatch.setattr("app.review_pr_async", fake_review)
    monkeypatch.setattr("app.verify_github_signature", lambda raw, sig: True)
    monkeypatch.setattr("app.create_or_update_check_run", lambda *args, **kwargs: None)

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
