from __future__ import annotations

import json
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class VectorDocument:
    """Represents a single chunk of repository context."""

    id: str
    file_path: str
    content: str
    embedding: List[float]


class JsonVectorStore:
    """Very small JSON-backed vector store for repository context chunks."""

    def __init__(self, storage_path: Path):
        self.path = storage_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata: Dict[str, str] = {}
        self._documents: List[VectorDocument] = []
        self._docs_by_path: Dict[str, List[VectorDocument]] = {}

    # -------- persistence -------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self.metadata = {}
            self._documents = []
            self._docs_by_path = {}
            return

        data = json.loads(self.path.read_text())
        self.metadata = data.get("metadata", {})
        docs = []
        for raw in data.get("documents", []):
            docs.append(
                VectorDocument(
                    id=raw["id"],
                    file_path=raw["file_path"],
                    content=raw["content"],
                    embedding=raw["embedding"],
                )
            )
        self._set_documents(docs)

    def persist(self) -> None:
        payload = {
            "metadata": self.metadata,
            "documents": [
                {
                    "id": doc.id,
                    "file_path": doc.file_path,
                    "content": doc.content,
                    "embedding": doc.embedding,
                }
                for doc in self._documents
            ],
        }
        self.path.write_text(json.dumps(payload))

    # -------- document management ----------------------------------------
    def _set_documents(self, docs: List[VectorDocument]) -> None:
        self._documents = docs
        self._docs_by_path = {}
        for doc in docs:
            self._docs_by_path.setdefault(doc.file_path, []).append(doc)

    def replace_all(self, docs: Iterable[VectorDocument], metadata: Optional[Dict[str, str]] = None) -> None:
        docs_list = list(docs)
        self._set_documents(docs_list)
        if metadata is not None:
            self.metadata = metadata

    def add_documents(self, docs: Iterable[VectorDocument]) -> None:
        for doc in docs:
            self._documents.append(doc)
            self._docs_by_path.setdefault(doc.file_path, []).append(doc)

    def has_path(self, path: str) -> bool:
        return path in self._docs_by_path

    def documents_for_path(self, path: str) -> List[VectorDocument]:
        return list(self._docs_by_path.get(path, []))

    @property
    def documents(self) -> List[VectorDocument]:
        return list(self._documents)

    # -------- similarity search ------------------------------------------
    def similarity_search(self, query_embedding: List[float], top_k: int = 4) -> List[VectorDocument]:
        if not self._documents:
            return []
        scored = [
            (self._cosine_similarity(query_embedding, doc.embedding), doc)
            for doc in self._documents
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sqrt(sum(x * x for x in a)) or 1.0
        norm_b = sqrt(sum(y * y for y in b)) or 1.0
        return dot / (norm_a * norm_b)
