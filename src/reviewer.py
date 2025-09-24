from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from openai import OpenAI

from .settings import CONTEXT_TOP_K, OPENAI_MODEL
from .context.service import RepositoryContextService, RetrievalRequest

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior staff engineer doing code review.
Evaluate diffs for:
1) Correctness & security (injection, secrets, auth, crypto)
2) Reliability & concurrency
3) Performance & complexity
4) Testing impact (missing/updated tests)
5) Maintainability & style

Output strict JSON for each file:
- file
- summary
- findings: array[{severity, title, lines, anchor, rationale, recommendation, patch}]
- severity MUST be exactly one of: BLOCKER, WARNING, NIT
""".strip()


@dataclass
class ReviewConfig:
    model: str
    context_top_k: int
    max_diff_chars: int = 70_000
    temperature: float = 0.2

    @classmethod
    def from_settings(cls) -> "ReviewConfig":
        return cls(model=OPENAI_MODEL, context_top_k=CONTEXT_TOP_K)


@dataclass
class ReviewChunk:
    file_path: str
    diff_text: str


class DiffParser:
    """Splits unified diffs into per-file chunks."""

    _FILE_PATTERN = re.compile(r"\sa/([^\s]+)\sb/([^\s]+)")

    def split(self, unified_diff: str) -> List[ReviewChunk]:
        parts = re.split(r"(?m)^diff --git ", unified_diff)
        chunks: List[ReviewChunk] = []
        for part in parts:
            if not part.strip():
                continue
            header = part.splitlines()[0]
            match = self._FILE_PATTERN.search("diff --git " + header)
            file_path = match.group(2) if match else header.strip()
            chunk_text = "diff --git " + part if not part.startswith("diff --git ") else part
            chunks.append(ReviewChunk(file_path=file_path, diff_text=chunk_text))
        return chunks


class ReviewEngine:
    """Coordinates PR review generation and context retrieval."""

    def __init__(
        self,
        config: ReviewConfig,
        openai_client: Optional[OpenAI] = None,
        diff_parser: Optional[DiffParser] = None,
    ) -> None:
        self._config = config
        self._client = openai_client or OpenAI()
        self._diff_parser = diff_parser or DiffParser()

    def review(
        self,
        pr_title: str,
        unified_diff: str,
        *,
        max_files: int = 25,
        context_service: Optional[RepositoryContextService] = None,
    ) -> Dict[str, Any]:
        chunks = self._diff_parser.split(unified_diff)
        if not chunks:
            return {
                "summary": "No diff to review.",
                "files": [],
                "findings_total": 0,
                "per_file_diffs": {},
            }

        selected = chunks[:max_files]
        per_file_diffs = {chunk.file_path: chunk.diff_text for chunk in selected}

        if context_service:
            try:
                context_service.ensure_index([chunk.file_path for chunk in selected])
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("Context indexing failed; continuing without repository context: %s", exc)
                context_service = None

        file_reviews: List[Dict[str, Any]] = []
        for chunk in selected:
            contexts: Optional[List[str]] = None
            if context_service:
                try:
                    contexts = context_service.retrieve_context(
                        RetrievalRequest(
                            file_path=chunk.file_path,
                            diff_text=chunk.diff_text,
                            top_k=self._config.context_top_k,
                        )
                    )
                except Exception as exc:  # pragma: no cover - defensive fallback
                    logger.warning("Context retrieval failed for %s: %s", chunk.file_path, exc)
                    contexts = None

            file_review = self._review_single_file(
                pr_title=pr_title,
                file_path=chunk.file_path,
                diff_text=chunk.diff_text,
                context_blocks=contexts,
            )
            file_reviews.append(file_review)

        summary = self._build_summary(file_reviews)
        return {
            "summary": summary,
            "files": file_reviews,
            "findings_total": self._count_findings(file_reviews),
            "per_file_diffs": per_file_diffs,
        }

    # ------------------------------------------------------------------
    def _review_single_file(
        self,
        *,
        pr_title: str,
        file_path: str,
        diff_text: str,
        context_blocks: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        context_section = ""
        if context_blocks:
            cleaned = "\n\n---\n\n".join(block.strip() for block in context_blocks if block.strip())
            if cleaned:
                context_section = f"\n\nAdditional repository context:\n{cleaned}"

        instructions = f"""
Review the following single-file unified diff in the context of the PR title.

Rules for 'anchor':
- Choose one exact line from the DIFF that best represents the issue location.
- Prefer lines beginning with '+'. If none, choose a context line starting with a single space.
- Copy the line content after the sign exactly.
- Never use removed '-' lines as anchors.

Return strict JSON with keys: file, summary, findings[{{severity,title,lines,anchor,rationale,recommendation,patch}}].
Remember: use BLOCKER, WARNING, or NIT for `severity`.

PR_TITLE: {pr_title}
FILE: {file_path}
DIFF:
{diff_text[: self._config.max_diff_chars]}
{context_section}
""".strip()

        response = self._client.chat.completions.create(
            model=self._config.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": instructions},
            ],
            temperature=self._config.temperature,
        )
        payload = response.choices[0].message.content
        try:
            data = json.loads(payload)
        except Exception:  # pragma: no cover - defensive fallback
            data = {"file": file_path, "summary": "", "findings": []}
        data.setdefault("file", file_path)
        data.setdefault("summary", "")
        data.setdefault("findings", [])
        return data

    # ------------------------------------------------------------------
    @staticmethod
    def _build_summary(file_reviews: Sequence[Dict[str, Any]]) -> str:
        blocker_count = sum(1 for review in file_reviews for item in review.get("findings", []) if item.get("severity") == "BLOCKER")
        warning_count = sum(1 for review in file_reviews for item in review.get("findings", []) if item.get("severity") == "WARNING")
        nit_count = sum(1 for review in file_reviews for item in review.get("findings", []) if item.get("severity") == "NIT")
        return (
            f"Reviewed {len(file_reviews)} file(s). "
            f"Found {blocker_count} blocker(s), {warning_count} warning(s), {nit_count} nit(s)."
        )

    @staticmethod
    def _count_findings(file_reviews: Sequence[Dict[str, Any]]) -> int:
        return sum(len(review.get("findings", [])) for review in file_reviews)


_default_engine = ReviewEngine(ReviewConfig.from_settings())


def review_pr(
    pr_title: str,
    unified_diff: str,
    max_files: int = 25,
    context_service: Optional[RepositoryContextService] = None,
) -> Dict[str, Any]:
    """Backwards-compatible entry point used by FastAPI routes."""

    return _default_engine.review(
        pr_title,
        unified_diff,
        max_files=max_files,
        context_service=context_service,
    )
