from __future__ import annotations

import ast
import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import requests
from openai import OpenAI

from .context_store import FaissVectorStore, VectorDocument
from .reranker import OpenAIReranker
from .security import get_installation_token
from .settings import (
    CONTEXT_ENABLE_RERANKER,
    CONTEXT_INDEX_DIR,
    CONTEXT_MAX_CHARS_PER_CHUNK,
    CONTEXT_MAX_FILE_BYTES,
    CONTEXT_MAX_FILES,
    CONTEXT_RERANK_MAX_CHARS,
    CONTEXT_RERANK_MODEL,
    CONTEXT_RETRIEVAL_CANDIDATES,
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
EMBED_BATCH_SIZE = 32
TEXT_CHUNK_OVERLAP = 200

GENERIC_CODE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+)?(?:async\s+)?"
    r"(?:function|class|interface|struct|enum|fn|func)\b",
    re.IGNORECASE,
)


@dataclass
class ChunkPiece:
    text: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    label: Optional[str] = None


def _is_interesting_path(path: str) -> bool:
    upper = path.upper()
    if upper.startswith("README") or "CONTRIBUTING" in upper:
        return True
    if any(path.startswith(prefix) for prefix in PREFERRED_DIRECTORIES):
        return True
    ext = Path(path).suffix.lower()
    return ext in DOC_EXTENSIONS or ext in CODE_EXTENSIONS


def _trim(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _chunk_text(text: str, max_chars: int, overlap: int) -> List[str]:
    if not text:
        return []
    sanitized = text.replace("\r\n", "\n")
    if len(sanitized) <= max_chars:
        return [sanitized]
    chunks: List[str] = []
    step = max(max_chars - overlap, 1)
    for start in range(0, len(sanitized), step):
        chunk = sanitized[start : start + max_chars]
        if chunk:
            chunks.append(chunk)
    return chunks


def _slice_lines(lines: List[str], start: int, end: int) -> str:
    start = max(start, 1)
    end = max(end, start)
    return "\n".join(lines[start - 1 : end]).strip("\n")


def _chunk_python_ast(text: str) -> List[ChunkPiece]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [ChunkPiece(text=text)]

    lines = text.splitlines()
    segments: List[tuple[int, int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            label = f"function {node.name}"
        elif isinstance(node, ast.ClassDef):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            label = f"class {node.name}"
        else:
            continue
        segments.append((start, end, label))

    segments.sort(key=lambda item: item[0])
    pieces: List[ChunkPiece] = []
    cursor = 1
    for start, end, label in segments:
        if start > cursor:
            preamble = _slice_lines(lines, cursor, start - 1)
            if preamble.strip():
                pieces.append(ChunkPiece(text=preamble, start_line=cursor, end_line=start - 1, label="module"))
        block = _slice_lines(lines, start, end)
        if block.strip():
            pieces.append(ChunkPiece(text=block, start_line=start, end_line=end, label=label))
        cursor = end + 1
    if cursor <= len(lines):
        tail = _slice_lines(lines, cursor, len(lines))
        if tail.strip():
            pieces.append(ChunkPiece(text=tail, start_line=cursor, end_line=len(lines), label="module"))
    return pieces or [ChunkPiece(text=text)]


def _chunk_generic_code(text: str) -> List[ChunkPiece]:
    lines = text.splitlines()
    markers: List[tuple[int, str]] = []
    for idx, line in enumerate(lines, 1):
        if GENERIC_CODE_PATTERN.match(line):
            markers.append((idx, line.strip()))
    if not markers:
        return [ChunkPiece(text=text)]

    pieces: List[ChunkPiece] = []
    if markers[0][0] > 1:
        pre = _slice_lines(lines, 1, markers[0][0] - 1)
        if pre.strip():
            pieces.append(ChunkPiece(text=pre, start_line=1, end_line=markers[0][0] - 1, label="module"))
    for (start, label), next_marker in zip(markers, markers[1:] + [(len(lines) + 1, "")]):
        end = next_marker[0] - 1
        code_block = _slice_lines(lines, start, end)
        if not code_block.strip():
            continue
        pieces.append(ChunkPiece(text=code_block, start_line=start, end_line=end, label=label or "symbol"))
    return pieces or [ChunkPiece(text=text)]


def _chunk_file(path: str, text: str) -> List[ChunkPiece]:
    ext = Path(path).suffix.lower()
    if ext in DOC_EXTENSIONS or path.upper().startswith("README"):
        doc_chunks = _chunk_text(text, CONTEXT_MAX_CHARS_PER_CHUNK, TEXT_CHUNK_OVERLAP)
        if not doc_chunks:
            doc_chunks = [text]
        return [ChunkPiece(text=chunk) for chunk in doc_chunks]
    if ext == ".py":
        return _chunk_python_ast(text)
    if ext in CODE_EXTENSIONS:
        return _chunk_generic_code(text)
    fallback_chunks = _chunk_text(text, CONTEXT_MAX_CHARS_PER_CHUNK, TEXT_CHUNK_OVERLAP)
    if not fallback_chunks:
        fallback_chunks = [text]
    return [ChunkPiece(text=chunk) for chunk in fallback_chunks]


def _format_context_block(doc: VectorDocument) -> str:
    location = doc.file_path
    if doc.start_line is not None:
        location += f":{doc.start_line}"
        if doc.end_line and doc.end_line != doc.start_line:
            location += f"-{doc.end_line}"
    header = f"[Source: {location}]"
    if doc.label:
        header += f" ({doc.label})"
    return f"{header}\n{doc.content}"


@dataclass
class RepoContextProvider:
    owner: str
    repo: str
    base_sha: str
    pr_title: str

    def __post_init__(self) -> None:
        self.client = OpenAI()
        store_dir = Path(CONTEXT_INDEX_DIR) / f"{self.owner}__{self.repo}"
        self.store = FaissVectorStore(store_dir)
        self.store.load()
        self.reranker = None
        if CONTEXT_ENABLE_RERANKER:
            self.reranker = OpenAIReranker(
                model=CONTEXT_RERANK_MODEL,
                max_chars=CONTEXT_RERANK_MAX_CHARS,
            )

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

        candidate_k = max(top_k, CONTEXT_RETRIEVAL_CANDIDATES)
        docs = self.store.similarity_search(embedding, top_k=candidate_k)
        if not docs:
            return []

        if self.reranker:
            try:
                docs = self.reranker.rerank(query, docs, top_k)
            except Exception as exc:
                logger.warning("Context reranker failed; falling back to embedding order: %s", exc)
                docs = docs[:top_k]
        else:
            docs = docs[:top_k]

        formatted = [
            _trim(_format_context_block(doc), CONTEXT_MAX_CHARS_PER_CHUNK + 200)
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

    def _ingest_additional_paths(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        token = get_installation_token(self.owner, self.repo)
        docs = self._ingest_paths(paths, token)
        if not docs:
            return
        self.store.add_documents(docs)

    def _ingest_paths(self, paths: Iterable[str], token: str) -> List[VectorDocument]:
        documents: List[VectorDocument] = []
        texts: List[str] = []
        total_files = 0
        for path in paths:
            content = self._fetch_file_content(path, token)
            if content is None:
                continue
            total_files += 1
            for idx, chunk in enumerate(_chunk_file(path, content)):
                chunk_text = chunk.text.strip()
                if not chunk_text:
                    continue
                doc_id = f"{path}::chunk-{idx}"
                trimmed_text = _trim(chunk_text, CONTEXT_MAX_CHARS_PER_CHUNK)
                doc = VectorDocument(
                    id=doc_id,
                    file_path=path,
                    content=trimmed_text,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    label=chunk.label,
                    embedding=None,
                )
                documents.append(doc)
                texts.append(trimmed_text)
        if not texts:
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
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
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
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            response = self.client.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=batch)
            embeddings.extend([item.embedding for item in response.data])
        return embeddings
