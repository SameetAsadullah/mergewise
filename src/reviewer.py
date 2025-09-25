from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from openai import AsyncOpenAI

from .settings import CONTEXT_TOP_K, OPENAI_MODEL
from .context.service import RepositoryContextService, RetrievalRequest
from .review_models import FileReviewModel, ReviewResultModel
from .review_models import ReviewResultModel

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
        async_client: Optional[AsyncOpenAI] = None,
        diff_parser: Optional[DiffParser] = None,
    ) -> None:
        self._config = config
        self._async_client = async_client or AsyncOpenAI()
        self._diff_parser = diff_parser or DiffParser()

    def review(
        self,
        pr_title: str,
        unified_diff: str,
        *,
        max_files: int = 25,
        context_service: Optional[RepositoryContextService] = None,
    ) -> Dict[str, Any]:
        return asyncio.run(
            self.review_async(
                pr_title,
                unified_diff,
                max_files=max_files,
                context_service=context_service,
            )
        )

    async def review_async(
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
                logger.warning("Context indexing failed; continuing without context: %s", exc)
                context_service = None

        async def process(chunk: ReviewChunk) -> Dict[str, Any]:
            contexts: Optional[List[str]] = None
            if context_service:
                try:
                    contexts = await asyncio.to_thread(
                        context_service.retrieve_context,
                        RetrievalRequest(
                            file_path=chunk.file_path,
                            diff_text=chunk.diff_text,
                            top_k=self._config.context_top_k,
                        ),
                    )
                except Exception as exc:  # pragma: no cover - defensive fallback
                    logger.warning("Context retrieval failed for %s: %s", chunk.file_path, exc)
                    contexts = None

            return await self._review_single_file_async(
                pr_title=pr_title,
                file_path=chunk.file_path,
                diff_text=chunk.diff_text,
                context_blocks=contexts,
            )

        raw_reviews = await asyncio.gather(*(process(chunk) for chunk in selected))
        file_reviews = [FileReviewModel(**item).model_dump() for item in raw_reviews]
        summary = self._build_summary(file_reviews)
        result_model = ReviewResultModel(
            summary=summary,
            files=[FileReviewModel(**review) for review in file_reviews],
            findings_total=self._count_findings(file_reviews),
            per_file_diffs=per_file_diffs,
        )
        return result_model.model_dump()

    # ------------------------------------------------------------------
    async def _review_single_file_async(
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
If you propose a code change, include it in `patch` as a minimal unified diff that can be applied directly.

PR_TITLE: {pr_title}
FILE: {file_path}
DIFF:
{diff_text[: self._config.max_diff_chars]}
{context_section}
""".strip()

        response = await self._async_client.chat.completions.create(
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
            data = FileReviewModel.model_validate_json(payload)
            validated = data.model_dump()
        except Exception:  # pragma: no cover - defensive fallback
            logger.warning("Failed to parse review response for %s", file_path)
            validated = {"file": file_path, "summary": "", "findings": []}
        validated.setdefault("file", file_path)
        validated.setdefault("summary", "")
        validated.setdefault("findings", [])
        return validated

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


async def review_pr_async(
    pr_title: str,
    unified_diff: str,
    max_files: int = 25,
    context_service: Optional[RepositoryContextService] = None,
) -> Dict[str, Any]:
    """Async entry point used by FastAPI routes."""

    return await _default_engine.review_async(
        pr_title,
        unified_diff,
        max_files=max_files,
        context_service=context_service,
    )


def review_pr(
    pr_title: str,
    unified_diff: str,
    max_files: int = 25,
    context_service: Optional[RepositoryContextService] = None,
) -> Dict[str, Any]:
    """Synchronous helper for legacy usage."""
    return _default_engine.review(
        pr_title,
        unified_diff,
        max_files=max_files,
        context_service=context_service,
    )
