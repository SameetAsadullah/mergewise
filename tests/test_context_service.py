from __future__ import annotations

import base64
from typing import Dict

import pytest

from tests.conftest import FakeOpenAI
from src.context.config import ContextConfig
from src.context.service import RepositoryContextService, RetrievalRequest


@pytest.fixture
def context_config(tmp_path) -> ContextConfig:
    return ContextConfig(
        index_root=tmp_path,
        max_chars_per_chunk=120,
        max_file_bytes=10_000,
        max_files=10,
        retrieval_candidates=5,
        top_k=3,
        enable_reranker=False,
        rerank_model="fake",
        rerank_max_chars=200,
        embedding_model="fake-embed",
        text_chunk_overlap=10,
        embedding_batch_size=32,
    )


@pytest.fixture
def fake_tree_response():
    return {
        "tree": [
            {"path": "docs/readme.md", "type": "blob", "size": 120},
            {"path": "src/app.py", "type": "blob", "size": 140},
        ]
    }


@pytest.fixture
def fake_file_contents():
    python_content = """\
def add(a, b):
    return a + b
"""
    doc_content = "Project documentation." * 3
    return {
        "docs/readme.md": base64.b64encode(doc_content.encode()).decode(),
        "src/app.py": base64.b64encode(python_content.encode()).decode(),
        "src/new_file.py": base64.b64encode("print('hi')".encode()).decode(),
    }


@pytest.fixture
def mock_requests(monkeypatch, fake_tree_response, fake_file_contents):
    class Response:
        def __init__(self, json_payload: Dict[str, object], status_code: int = 200):
            self._payload = json_payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not (200 <= self.status_code < 300):
                raise RuntimeError("HTTP error")

    def fake_get(url, headers=None, params=None, timeout=None):  # noqa: D401
        if "/git/trees/" in url:
            return Response(fake_tree_response)
        if "/contents/" in url:
            path = url.split("/contents/")[-1]
            encoded = fake_file_contents[path]
            return Response({"encoding": "base64", "content": encoded})
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr("src.context.service.requests.get", fake_get)


@pytest.fixture
def mock_installation_token(monkeypatch):
    monkeypatch.setattr("src.context.service.get_installation_token", lambda owner, repo: "token")


def test_repository_context_service_index_and_retrieve(
    context_config,
    mock_requests,
    mock_installation_token,
):
    openai = FakeOpenAI()
    service = RepositoryContextService(
        owner="acme",
        repo="demo",
        base_sha="abc123",
        pr_title="Add feature",
        config=context_config,
        openai_client=openai,
    )

    service.ensure_index(["src/app.py"])
    assert service._store.metadata["commit_sha"] == "abc123"
    assert service._store.documents

    results = service.retrieve_context(
        RetrievalRequest(file_path="src/app.py", diff_text="+ add call", top_k=2)
    )
    assert results
    assert any("src/app.py" in block for block in results)


def test_repository_context_service_incremental_ingest(
    context_config,
    mock_requests,
    mock_installation_token,
):
    openai = FakeOpenAI()
    service = RepositoryContextService(
        owner="acme",
        repo="demo",
        base_sha="abc123",
        pr_title="Add feature",
        config=context_config,
        openai_client=openai,
    )
    service.ensure_index(["src/app.py"])

    # simulating second ensure with same sha but missing path to trigger incremental ingest
    service.ensure_index(["src/new_file.py"])
    # Because requests.get for new file would fail, store should still have docs without raising
    assert service._store.documents
