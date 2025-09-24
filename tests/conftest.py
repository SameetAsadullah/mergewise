from __future__ import annotations

import base64
import json
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

import pytest

warnings.filterwarnings(
    "ignore",
    message="urllib3 v2 only supports OpenSSL 1.1.1+",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message="builtin type Swig",
    category=DeprecationWarning,
)


@dataclass
class FakeEmbeddingItem:
    embedding: List[float]


@dataclass
class FakeEmbeddingResponse:
    data: List[FakeEmbeddingItem]


@dataclass
class FakeChatChoiceMessage:
    content: str


@dataclass
class FakeChatChoice:
    message: FakeChatChoiceMessage


@dataclass
class FakeChatResponse:
    choices: List[FakeChatChoice]


class FakeOpenAI:
    """Simple deterministic OpenAI stub for tests."""

    def __init__(self, *, completion_payloads: Iterable[Dict[str, Any]] | None = None) -> None:
        self._completion_payloads = list(completion_payloads or [])
        self.embeddings = type("Embeddings", (), {"create": self._create_embeddings})()
        self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": self._create_completion})()})()

    # ------------------------------------------------------------------
    def _create_embeddings(self, *, model: str, input: List[str]) -> FakeEmbeddingResponse:  # noqa: D401
        def encode(text: str) -> List[float]:
            total = sum(ord(ch) for ch in text)
            length = float(len(text)) or 1.0
            return [length, float(total % 97), float((total // 97) % 97), float(total % 13)]

        return FakeEmbeddingResponse(data=[FakeEmbeddingItem(embedding=encode(text)) for text in input])

    def _create_completion(self, *, model: str, response_format: Dict[str, Any], messages: List[Dict[str, str]], temperature: float = 0.0) -> FakeChatResponse:  # noqa: D401
        if self._completion_payloads:
            payload = self._completion_payloads.pop(0)
        else:
            payload = {"file": "test.py", "summary": "", "findings": []}
        content = json.dumps(payload)
        return FakeChatResponse(choices=[FakeChatChoice(message=FakeChatChoiceMessage(content=content))])


@pytest.fixture
def sample_python_file() -> str:
    return """\
class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello {self.name}!"


def helper():
    return "ok"
"""


@pytest.fixture
def sample_js_file() -> str:
    return """\
export function sum(a, b) {
  return a + b;
}

export class Counter {
  constructor() {
    this.total = 0;
  }
}
"""


@pytest.fixture
def encoded_content(sample_python_file: str) -> str:
    return base64.b64encode(sample_python_file.encode("utf-8")).decode("utf-8")
