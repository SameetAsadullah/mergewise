from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import requests
from openai import OpenAI

from ..security import get_installation_token
from ..settings import ENABLE_CONTEXT_INDEXING, GITHUB_API_BASE
from .chunking import ContextChunker
from .config import ContextConfig
from .reranking import ContextReranker, OpenAIReranker
from .store import FaissVectorStore, VectorDocument

logger = logging.getLogger(__name__)


@dataclass
class RetrievalRequest:
    file_path: str
    diff_text: str
    top_k: int


class RepositoryContextService:
    """Builds and queries repository context indexes for PR reviews."""

    def __init__(
        self,
        owner: str,
        repo: str,
        base_sha: str,
        pr_title: str,
        config: ContextConfig,
        openai_client: Optional[OpenAI] = None,
        chunker: Optional[ContextChunker] = None,
        reranker: Optional[ContextReranker] = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.base_sha = base_sha
        self.pr_title = pr_title
        self.config = config

        self._openai = openai_client or OpenAI()
        self._chunker = chunker or ContextChunker(config.max_chars_per_chunk, config.text_chunk_overlap)
        self._store = FaissVectorStore(config.index_root / f"{owner}__{repo}")
        self._store.load()

        if reranker is not None:
            self._reranker = reranker
        elif config.enable_reranker:
            self._reranker = OpenAIReranker(self._openai, config.rerank_model, config.rerank_max_chars)
        else:
            self._reranker = None

    # ------------------------------------------------------------------
    def ensure_index(self, target_paths: Sequence[str]) -> None:
        if not ENABLE_CONTEXT_INDEXING:
            return
        stored_sha = self._store.metadata.get("commit_sha")
        if stored_sha != self.base_sha:
            logger.info(
                "Rebuilding context index for %s/%s at %s",
                self.owner,
                self.repo,
                self.base_sha,
            )
            self._rebuild_index(target_paths)
        else:
            missing = [path for path in target_paths if not self._store.has_path(path)]
            if missing:
                logger.info(
                    "Indexing %s additional files for %s/%s",
                    len(missing),
                    self.owner,
                    self.repo,
                )
                self._ingest_additional_paths(missing)

    # ------------------------------------------------------------------
    def retrieve_context(self, request: RetrievalRequest) -> List[str]:
        if not ENABLE_CONTEXT_INDEXING or not self._store.documents:
            return []

        query = self._trim(
            f"PR Title: {self.pr_title}\nFile: {request.file_path}\nDiff snippet:\n{request.diff_text}"
        )
        embedding = self._embed([query])[0]

        candidate_k = max(request.top_k, self.config.retrieval_candidates)
        candidates = self._store.similarity_search(embedding, top_k=candidate_k)
        if not candidates:
            return []

        if self._reranker:
            try:
                candidates = self._reranker.rerank(query, candidates, request.top_k)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("Context reranker failed; using embedding scores: %s", exc)
                candidates = candidates[: request.top_k]
        else:
            candidates = candidates[: request.top_k]

        return [self._format_context_block(doc) for doc in candidates]

    # ------------------------------------------------------------------
    def _rebuild_index(self, target_paths: Sequence[str]) -> None:
        token = get_installation_token(self.owner, self.repo)
        tree = self._fetch_repo_tree(token)
        interesting_paths = self._select_paths(tree, target_paths)
        documents = self._ingest_paths(interesting_paths, token)
        metadata = {
            "commit_sha": self.base_sha,
            "owner": self.owner,
            "repo": self.repo,
        }
        self._store.replace_all(documents, metadata=metadata)

    def _ingest_additional_paths(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        token = get_installation_token(self.owner, self.repo)
        documents = self._ingest_paths(paths, token)
        if documents:
            self._store.add_documents(documents)

    # ------------------------------------------------------------------
    def _ingest_paths(self, paths: Iterable[str], token: str) -> List[VectorDocument]:
        documents: List[VectorDocument] = []
        texts: List[str] = []
        total_files = 0
        for path in paths:
            content = self._fetch_file_content(path, token)
            if content is None:
                continue
            total_files += 1
            for idx, piece in enumerate(self._chunker.chunk(path, content)):
                chunk_text = piece.text.strip()
                if not chunk_text:
                    continue
                chunk_id = f"{path}::chunk-{idx}"
                trimmed = self._trim(chunk_text)
                documents.append(
                    VectorDocument(
                        id=chunk_id,
                        file_path=path,
                        content=trimmed,
                        start_line=piece.start_line,
                        end_line=piece.end_line,
                        label=piece.label,
                        embedding=None,
                    )
                )
                texts.append(trimmed)
        if not documents:
            return []
        embeddings = self._embed(texts)
        for doc, embedding in zip(documents, embeddings):
            doc.embedding = embedding
        logger.info(
            "Indexed %s chunks from %s files for %s/%s",
            len(documents),
            total_files,
            self.owner,
            self.repo,
        )
        return documents

    # ------------------------------------------------------------------
    def _fetch_repo_tree(self, token: str) -> List[dict]:
        url = f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}/git/trees/{self.base_sha}"
        params = {"recursive": "1"}
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params=params,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("tree", [])

    def _select_paths(self, tree: Iterable[dict], target_paths: Sequence[str]) -> List[str]:
        selected: List[str] = []
        seen = set()
        for path in target_paths:
            seen.add(path)
            selected.append(path)
        doc_paths: List[str] = []
        code_paths: List[str] = []
        for node in tree:
            if node.get("type") != "blob":
                continue
            path = node.get("path") or ""
            if not self._chunker.is_interesting_path(path):
                continue
            if node.get("size", 0) > self.config.max_file_bytes:
                continue
            if path in seen:
                continue
            if self._chunker.is_document_path(path):
                doc_paths.append(path)
            else:
                code_paths.append(path)
        for path in doc_paths:
            if len(selected) >= self.config.max_files:
                break
            selected.append(path)
        for path in code_paths:
            if len(selected) >= self.config.max_files:
                break
            selected.append(path)
        return selected

    def _fetch_file_content(self, path: str, token: str) -> Optional[str]:
        url = f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}/contents/{path}"
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"ref": self.base_sha},
            timeout=30,
        )
        if response.status_code == 404:
            logger.debug("File %s missing at %s", path, self.base_sha)
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("encoding") == "base64":
            try:
                decoded = base64.b64decode(payload.get("content", "")).decode("utf-8", errors="ignore")
                return decoded
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.debug("Failed to decode %s: %s", path, exc)
                return None
        if isinstance(payload.get("content"), str):
            return payload["content"]
        return None

    def _embed(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        batch_size = self.config.embedding_batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = self._openai.embeddings.create(model=self.config.embedding_model, input=batch)
            embeddings.extend([item.embedding for item in response.data])
        return embeddings

    def _trim(self, text: str) -> str:
        text = text.strip()
        if len(text) <= self.config.max_chars_per_chunk:
            return text
        return text[: self.config.max_chars_per_chunk - 3] + "..."

    def _format_context_block(self, doc: VectorDocument) -> str:
        location = doc.file_path
        if doc.start_line is not None:
            location += f":{doc.start_line}"
            if doc.end_line and doc.end_line != doc.start_line:
                location += f"-{doc.end_line}"
        header = f"[Source: {location}]"
        if doc.label:
            header += f" ({doc.label})"
        return f"{header}\n{doc.content}"
