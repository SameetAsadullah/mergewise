from __future__ import annotations

from typing import Dict

import pytest

from src.github import GitHubClient


class FakeResponse:
    def __init__(self, payload: Dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise RuntimeError("HTTP error")


@pytest.fixture
def mock_requests(monkeypatch):
    state = {"posted": [], "patched": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/pulls/42") and headers["Accept"] == "application/vnd.github+json":
            return FakeResponse({
                "title": "Add feature",
                "base": {"ref": "main", "sha": "base-sha"},
                "head": {"ref": "feature", "sha": "head-sha"},
            })
        if url.endswith("/pulls/42"):
            return FakeResponse("diff content")
        raise AssertionError(f"Unexpected GET {url}")

    def fake_post(url, headers=None, json=None, timeout=None):
        state["posted"].append(json)
        return FakeResponse({"id": 1})

    def fake_patch(url, headers=None, json=None, timeout=None):
        state["patched"].append(json)
        return FakeResponse({})

    monkeypatch.setattr("src.github.requests.get", fake_get)
    monkeypatch.setattr("src.github.requests.post", fake_post)
    monkeypatch.setattr("src.github.requests.patch", fake_patch)
    return state


@pytest.fixture(autouse=True)
def mock_token(monkeypatch):
    monkeypatch.setattr("src.github.get_installation_token", lambda owner, repo: "token")


def test_github_client_pull_request_details(mock_requests):
    client = GitHubClient("acme", "demo")
    details = client.get_pull_request_details(42)
    assert details.title == "Add feature"
    assert details.base_ref == "main"
    assert details.diff == "diff content"


def test_github_client_check_run_creation(mock_requests):
    client = GitHubClient("acme", "demo")
    client.create_or_update_check_run(
        head_sha="head",
        conclusion="neutral",
        summary_md="Summary",
        annotations=[{"path": "file.py", "message": "ok", "start_line": 1, "end_line": 1, "annotation_level": "notice", "title": "t"}],
    )
    assert mock_requests["posted"], "Check run creation not invoked"
    assert mock_requests["patched"], "Annotations not appended"
