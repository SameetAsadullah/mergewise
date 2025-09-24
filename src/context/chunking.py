from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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
PREFERRED_DIRECTORIES: tuple[str, ...] = (
    "docs/",
    "doc/",
    "documentation/",
    "src/",
    "lib/",
    "app/",
    "config/",
)

_GENERIC_CODE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+)?(?:async\s+)?"
    r"(?:function|class|interface|struct|enum|fn|func)\b",
    re.IGNORECASE,
)


@dataclass
class ChunkPiece:
    """Represents a logical chunk of repository content."""

    text: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    label: Optional[str] = None


class ContextChunker:
    """Generates semantic chunks for repository files."""

    def __init__(self, max_chars: int, overlap: int) -> None:
        self.max_chars = max_chars
        self.overlap = overlap

    # ------------------------------------------------------------------
    def is_interesting_path(self, path: str) -> bool:
        upper = path.upper()
        if upper.startswith("README") or "CONTRIBUTING" in upper:
            return True
        if any(path.startswith(prefix) for prefix in PREFERRED_DIRECTORIES):
            return True
        ext = Path(path).suffix.lower()
        return ext in DOC_EXTENSIONS or ext in CODE_EXTENSIONS

    def is_document_path(self, path: str) -> bool:
        upper = path.upper()
        if upper.startswith("README") or "CONTRIBUTING" in upper:
            return True
        return Path(path).suffix.lower() in DOC_EXTENSIONS

    def is_code_path(self, path: str) -> bool:
        ext = Path(path).suffix.lower()
        return ext in CODE_EXTENSIONS

    # ------------------------------------------------------------------
    def chunk(self, path: str, text: str) -> List[ChunkPiece]:
        ext = Path(path).suffix.lower()
        if ext in DOC_EXTENSIONS or path.upper().startswith("README"):
            return self._chunk_document(text)
        if ext == ".py":
            return self._chunk_python_ast(text)
        if ext in CODE_EXTENSIONS:
            return self._chunk_generic_code(text)
        return self._chunk_document(text)

    # ------------------------------------------------------------------
    def _chunk_document(self, text: str) -> List[ChunkPiece]:
        chunks = self._chunk_text(text)
        return [ChunkPiece(text=chunk) for chunk in chunks]

    def _chunk_python_ast(self, text: str) -> List[ChunkPiece]:
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
                segments.append((start, end, f"function {node.name}"))
            elif isinstance(node, ast.ClassDef):
                start = getattr(node, "lineno", 1)
                end = getattr(node, "end_lineno", start)
                segments.append((start, end, f"class {node.name}"))

        segments.sort(key=lambda item: item[0])
        pieces: List[ChunkPiece] = []
        cursor = 1
        for start, end, label in segments:
            if start > cursor:
                preamble = _slice_lines(lines, cursor, start - 1)
                if preamble.strip():
                    pieces.append(ChunkPiece(text=preamble, start_line=cursor, end_line=start - 1, label="module"))
            body = _slice_lines(lines, start, end)
            if body.strip():
                pieces.append(ChunkPiece(text=body, start_line=start, end_line=end, label=label))
            cursor = end + 1
        if cursor <= len(lines):
            tail = _slice_lines(lines, cursor, len(lines))
            if tail.strip():
                pieces.append(ChunkPiece(text=tail, start_line=cursor, end_line=len(lines), label="module"))
        return pieces or [ChunkPiece(text=text)]

    def _chunk_generic_code(self, text: str) -> List[ChunkPiece]:
        lines = text.splitlines()
        markers: List[tuple[int, str]] = []
        for idx, line in enumerate(lines, 1):
            if _GENERIC_CODE_PATTERN.match(line):
                markers.append((idx, line.strip()))
        if not markers:
            return [ChunkPiece(text=text)]

        pieces: List[ChunkPiece] = []
        if markers[0][0] > 1:
            prefix = _slice_lines(lines, 1, markers[0][0] - 1)
            if prefix.strip():
                pieces.append(ChunkPiece(text=prefix, start_line=1, end_line=markers[0][0] - 1, label="module"))
        for (start, label), following in zip(markers, markers[1:] + [(len(lines) + 1, "")]):
            end = following[0] - 1
            block = _slice_lines(lines, start, end)
            if not block.strip():
                continue
            pieces.append(ChunkPiece(text=block, start_line=start, end_line=end, label=label or "symbol"))
        return pieces or [ChunkPiece(text=text)]

    # ------------------------------------------------------------------
    def _chunk_text(self, text: str) -> List[str]:
        cleaned = text.replace("\r\n", "\n")
        if len(cleaned) <= self.max_chars:
            return [cleaned]
        chunks: List[str] = []
        step = max(self.max_chars - self.overlap, 1)
        for start in range(0, len(cleaned), step):
            fragment = cleaned[start : start + self.max_chars]
            if fragment:
                chunks.append(fragment)
        return chunks or [cleaned]


def _slice_lines(lines: List[str], start: int, end: int) -> str:
    start = max(start, 1)
    end = max(end, start)
    return "\n".join(lines[start - 1 : end]).strip("\n")
