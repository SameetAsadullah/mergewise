from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import faiss
import numpy as np


@dataclass
class VectorDocument:
    """Chunk of repository context stored alongside its metadata."""

    id: str
    file_path: str
    content: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    label: Optional[str] = None
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "VectorDocument":
        return cls(
            id=payload["id"],
            file_path=payload["file_path"],
            content=payload["content"],
            start_line=payload.get("start_line"),
            end_line=payload.get("end_line"),
            label=payload.get("label"),
            embedding=None,
        )


class FaissVectorStore:
    """FAISS-backed vector store that persists index + metadata to disk."""

    def __init__(self, storage_dir: Path):
        self.dir = storage_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.faiss"
        self.meta_path = self.dir / "metadata.json"

        self.metadata: Dict[str, str] = {}
        self._documents: List[VectorDocument] = []
        self._docs_by_path: Dict[str, List[VectorDocument]] = {}
        self._index: Optional[faiss.Index] = None
        self._dim: Optional[int] = None

    # ------------------------------------------------------------------
    def load(self) -> None:
        if self.meta_path.exists():
            data = json.loads(self.meta_path.read_text())
            self.metadata = data.get("metadata", {})
            docs = [VectorDocument.from_dict(item) for item in data.get("documents", [])]
            self._set_documents(docs)
        if self.index_path.exists():
            self._index = faiss.read_index(str(self.index_path))
            self._dim = self._index.d
        else:
            self._index = None
            self._dim = None

    # ------------------------------------------------------------------
    def replace_all(self, docs: Iterable[VectorDocument], metadata: Optional[Dict[str, str]] = None) -> None:
        docs_list = list(docs)
        if not docs_list:
            self._documents = []
            self._docs_by_path = {}
            self._index = None
            self._dim = None
            if metadata is not None:
                self.metadata = metadata
            self._persist()
            return

        dim = _embedding_dimension(docs_list)
        embeddings = self._prepare_embeddings(docs_list, dim)

        self._index = faiss.IndexFlatIP(dim)
        self._index.add(embeddings)
        self._dim = dim

        for doc in docs_list:
            doc.embedding = None
        self._set_documents(docs_list)

        if metadata is not None:
            self.metadata = metadata
        self._persist()

    def add_documents(self, docs: Iterable[VectorDocument]) -> None:
        docs_list = list(docs)
        if not docs_list:
            return
        if self._index is None or self._dim is None:
            self.replace_all(docs_list, metadata=self.metadata)
            return

        dim = _embedding_dimension(docs_list)
        if dim != self._dim:
            raise ValueError(f"Embedding dimension mismatch: expected {self._dim}, got {dim}")

        embeddings = self._prepare_embeddings(docs_list, dim)
        self._index.add(embeddings)

        for doc in docs_list:
            doc.embedding = None
        self._documents.extend(docs_list)
        self._rebuild_path_index()
        self._persist()

    # ------------------------------------------------------------------
    def has_path(self, path: str) -> bool:
        return path in self._docs_by_path

    def documents_for_path(self, path: str) -> List[VectorDocument]:
        return list(self._docs_by_path.get(path, []))

    @property
    def documents(self) -> List[VectorDocument]:
        return list(self._documents)

    # ------------------------------------------------------------------
    def similarity_search(self, query_embedding: List[float], top_k: int = 4) -> List[VectorDocument]:
        if self._index is None or not self._documents:
            return []
        query = _normalize(np.asarray(query_embedding, dtype=np.float32))
        query = np.expand_dims(query, 0)
        k = min(top_k, len(self._documents))
        if k <= 0:
            return []
        distances, indices = self._index.search(query, k)
        result: List[VectorDocument] = []
        for idx in indices[0]:
            if idx < 0:
                continue
            try:
                result.append(self._documents[idx])
            except IndexError:
                continue
        return result

    # ------------------------------------------------------------------
    def _prepare_embeddings(self, docs: List[VectorDocument], dim: int) -> np.ndarray:
        matrix = np.zeros((len(docs), dim), dtype=np.float32)
        for i, doc in enumerate(docs):
            if doc.embedding is None:
                raise ValueError("VectorDocument is missing embedding data")
            matrix[i] = _normalize(np.asarray(doc.embedding, dtype=np.float32))
        return matrix

    def _set_documents(self, docs: List[VectorDocument]) -> None:
        self._documents = docs
        self._rebuild_path_index()

    def _rebuild_path_index(self) -> None:
        self._docs_by_path = {}
        for doc in self._documents:
            self._docs_by_path.setdefault(doc.file_path, []).append(doc)

    def _persist(self) -> None:
        data = {
            "metadata": self.metadata,
            "documents": [doc.to_dict() for doc in self._documents],
        }
        self.meta_path.write_text(json.dumps(data))
        if self._index is not None:
            faiss.write_index(self._index, str(self.index_path))
        elif self.index_path.exists():
            self.index_path.unlink()


def _embedding_dimension(docs: List[VectorDocument]) -> int:
    for doc in docs:
        if doc.embedding:
            return len(doc.embedding)
    raise ValueError("At least one document must carry embedding data")


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec
    return vec / norm
