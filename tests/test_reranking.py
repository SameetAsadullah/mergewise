from __future__ import annotations

import pytest

from tests.conftest import FakeOpenAI
from src.context.reranking import OpenAIReranker
from src.context.store import VectorDocument


class RerankerClient(FakeOpenAI):
    def __init__(self, payload):
        super().__init__(completion_payloads=[payload])


@pytest.fixture
def sample_documents():
    return [
        VectorDocument(id="a", file_path="file.py", content="first", embedding=[1, 0, 0, 0]),
        VectorDocument(id="b", file_path="file.py", content="second", embedding=[0, 1, 0, 0]),
        VectorDocument(id="c", file_path="file.py", content="third", embedding=[0, 0, 1, 0]),
    ]


def test_reranker_uses_scores(sample_documents):
    payload = {
        "ranking": [
            {"id": "b", "score": 5},
            {"id": "c", "score": 3},
        ]
    }
    client = FakeOpenAI(completion_payloads=[payload])
    reranker = OpenAIReranker(client, model="fake", max_chars=50)

    reordered = reranker.rerank("query", sample_documents, top_k=2)
    assert [doc.id for doc in reordered] == ["b", "c"]


def test_reranker_fallback_on_error(sample_documents, monkeypatch):
    def failing_completion(*args, **kwargs):  # noqa: D401
        raise RuntimeError("boom")

    client = FakeOpenAI()
    client.chat.completions.create = failing_completion

    reranker = OpenAIReranker(client, model="fake")
    reordered = reranker.rerank("query", sample_documents, top_k=2)
    assert len(reordered) == 2
    assert [doc.id for doc in reordered] == ["a", "b"]
