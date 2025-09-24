from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import settings


@dataclass(frozen=True)
class ContextConfig:
    """Static configuration used by repository-context retrieval."""

    index_root: Path
    max_chars_per_chunk: int
    max_file_bytes: int
    max_files: int
    retrieval_candidates: int
    top_k: int
    enable_reranker: bool
    rerank_model: str
    rerank_max_chars: int
    embedding_model: str
    text_chunk_overlap: int = 200
    embedding_batch_size: int = 32

    @classmethod
    def from_settings(cls) -> "ContextConfig":
        return cls(
            index_root=Path(settings.CONTEXT_INDEX_DIR),
            max_chars_per_chunk=settings.CONTEXT_MAX_CHARS_PER_CHUNK,
            max_file_bytes=settings.CONTEXT_MAX_FILE_BYTES,
            max_files=settings.CONTEXT_MAX_FILES,
            retrieval_candidates=settings.CONTEXT_RETRIEVAL_CANDIDATES,
            top_k=settings.CONTEXT_TOP_K,
            enable_reranker=settings.CONTEXT_ENABLE_RERANKER,
            rerank_model=settings.CONTEXT_RERANK_MODEL,
            rerank_max_chars=settings.CONTEXT_RERANK_MAX_CHARS,
            embedding_model=settings.OPENAI_EMBEDDING_MODEL,
            text_chunk_overlap=200,
            embedding_batch_size=32,
        )
