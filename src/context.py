from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import requests
from openai import OpenAI

from .context_store import JsonVectorStore, VectorDocument
from .security import get_installation_token
from .settings import (
    CONTEXT_INDEX_DIR,
    CONTEXT_MAX_CHARS_PER_CHUNK,
    CONTEXT_MAX_FILE_BYTES,
    CONTEXT_MAX_FILES,
    CONTEXT_TOP_K,
    ENABLE_CONTEXT_INDEXING,
    GITHUB_API_BASE,
    OPENAI_EMBEDDING_MODEL,
)

logger = logging.getLogger(__name__)

DOC_EXTENSIONS = {".md", ".rst", ".txt"}
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".mjs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".go",
    ".java",
    ".rb",
    ".rs",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".cs",
    ".sql",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
}
PREFERRED_DIRECTORIES = ("docs/", "doc/", "documentation/", "src/", "lib/", "app/", "config/")
INDEX_BATCH_SIZE = 32
CHUNK_OVERLAP = 200


def _is_interesting_path(path: str) -> bool:
    upper = path.upper()
    if upper.startswith("README") or "CONTRIBUTING" in upper:
        return True
    if any(path.startswith(prefix) for prefix in PREFERRED_DIRECTORIES):
        return True
    ext = Path(path).suffix.lower()
    return ext in DOC_EXTENSIONS or ext in CODE_EXTENSIONS


def _chunk_text(text: str, max_chars: int, overlap: int) -> List[str]:
    if not text:
        return []
    sanitized = text.replace("\r\n", "\n")
    chunks: List[str] = []
    step = max(max_chars - overlap, 1)
    for start in range(0, len(sanitized), step):
        chunk = sanitized[start : start + max_chars]
        if chunk:
            chunks.append(chunk)
    return chunks


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


@dataclass
class RepoContextProvider:
    owner: str
    repo: str
    base_sha: str
    pr_title: str

    def __post_init__(self) -> None:
        self.client = OpenAI()
        store_path = Path(CONTEXT_INDEX_DIR) / f"{self.owner}__{self.repo}.json"
        self.store = JsonVectorStore(store_path)
        self.store.load()

    # ------------------------------------------------------------------
    def ensure_index(self, target_paths: Sequence[str]) -> None:
        if not ENABLE_CONTEXT_INDEXING:
            return
        stored_sha = self.store.metadata.get("commit_sha")
        if stored_sha != self.base_sha:
            logger.info("Rebuilding repository context index for %s/%s at %s", self.owner, self.repo, self.base_sha)
            self._rebuild_index(target_paths)
        else:
            missing = [path for path in target_paths if not self.store.has_path(path)]
            if missing:
                logger.info(
                    "Adding %s missing paths to existing context index for %s/%s",
                    len(missing),
                    self.owner,
                    self.repo,
                )
                self._ingest_additional_paths(missing)

    # ------------------------------------------------------------------
    def retrieve_context(self, file_path: str, diff_text: str, top_k: int = CONTEXT_TOP_K) -> List[str]:
        if not ENABLE_CONTEXT_INDEXING:
            return []
        if not self.store.documents:
            return []
        query = _trim(
            f"PR Title: {self.pr_title}\nFile: {file_path}\nDiff snippet:\n{diff_text}",
            CONTEXT_MAX_CHARS_PER_CHUNK,
        )
        embedding = self._embed([query])[0]
        docs = self.store.similarity_search(embedding, top_k=top_k)
        formatted = [
            _trim(f"[Source: {doc.file_path}]\n{doc.content}", CONTEXT_MAX_CHARS_PER_CHUNK)
            for doc in docs
        ]
        return formatted

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
        self.store.replace_all(documents, metadata=metadata)
        self.store.persist()

    def _ingest_additional_paths(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        token = get_installation_token(self.owner, self.repo)
        docs = self._ingest_paths(paths, token)
        if not docs:
            return
        self.store.add_documents(docs)
        self.store.persist()

    def _ingest_paths(self, paths: Iterable[str], token: str) -> List[VectorDocument]:
        chunks: List[VectorDocument] = []
        texts: List[str] = []
        doc_meta: List[tuple[str, str]] = []
        total_files = 0
        for path in paths:
            content = self._fetch_file_content(path, token)
            if content is None:
                continue
            total_files += 1
            for idx, chunk in enumerate(_chunk_text(content, CONTEXT_MAX_CHARS_PER_CHUNK, CHUNK_OVERLAP)):
                doc_id = f"{path}::chunk-{idx}"
                doc_meta.append((doc_id, path))
                texts.append(chunk)
        if not texts:
            return []
        embeddings = self._embed(texts)
        for (doc_id, path), chunk_text, embedding in zip(doc_meta, texts, embeddings):
            formatted = f"{path}\n\n{chunk_text.strip()}"
            chunks.append(VectorDocument(id=doc_id, file_path=path, content=formatted, embedding=embedding))
        logger.info("Indexed %s chunks from %s files for %s/%s", len(chunks), total_files, self.owner, self.repo)
        return chunks

    # ------------------------------------------------------------------
    def _fetch_repo_tree(self, token: str) -> List[dict]:
        url = f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}/git/trees/{self.base_sha}"
        params = {"recursive": "1"}
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get("tree", [])

    def _select_paths(self, tree: Iterable[dict], target_paths: Sequence[str]) -> List[str]:
        selected: List[str] = []
        seen = set()
        # Always prioritise target paths (files touched in the PR)
        for path in target_paths:
            seen.add(path)
            selected.append(path)
        # Then gather docs and code paths from the tree, respecting the limit.
        doc_paths: List[str] = []
        code_paths: List[str] = []
        for node in tree:
            if node.get("type") != "blob":
                continue
            path = node.get("path") or ""
            if not _is_interesting_path(path):
                continue
            if node.get("size", 0) > CONTEXT_MAX_FILE_BYTES:
                continue
            if path in seen:
                continue
            ext = Path(path).suffix.lower()
            if ext in DOC_EXTENSIONS or path.upper().startswith("README"):
                doc_paths.append(path)
            else:
                code_paths.append(path)
        # Limit docs first, then code up to the max.
        for path in doc_paths:
            if len(selected) >= CONTEXT_MAX_FILES:
                break
            selected.append(path)
        for path in code_paths:
            if len(selected) >= CONTEXT_MAX_FILES:
                break
            selected.append(path)
        return selected

    def _fetch_file_content(self, path: str, token: str) -> Optional[str]:
        url = f"{GITHUB_API_BASE}/repos/{self.owner}/{self.repo}/contents/{path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"ref": self.base_sha},
            timeout=30,
        )
        if resp.status_code == 404:
            logger.debug("File %s missing at %s", path, self.base_sha)
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            try:
                decoded = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
                return decoded
            except Exception as exc:
                logger.debug("Failed to decode %s: %s", path, exc)
                return None
        if isinstance(data.get("content"), str):
            return data["content"]
        return None

    def _embed(self, texts: List[str]) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for i in range(0, len(texts), INDEX_BATCH_SIZE):
            batch = texts[i : i + INDEX_BATCH_SIZE]
            response = self.client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=batch)
            embeddings.extend([item.embedding for item in response.data])
        return embeddings
