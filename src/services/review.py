from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..context import ContextConfig, RepositoryContextService
from ..github import create_or_update_check_run, get_pr_details
from ..reviewer import review_pr_async
from ..task_queue import enqueue_diff_review, enqueue_github_review
from ..utils import (
    build_check_summary_markdown,
    build_github_annotations,
    result_to_check_conclusion,
)


@dataclass
class ReviewOutcome:
    """Represents the result of a review request (queued or inline)."""

    task_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    queue_error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @property
    def queued(self) -> bool:
        return self.task_id is not None


class ReviewQueue:
    """Thin wrapper around Celery enqueue helpers."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enqueue_diff(self, pr_title: str, unified_diff: str, *, max_files: int = 25):
        if not self._enabled:
            raise RuntimeError("Task queue is disabled")
        return enqueue_diff_review(pr_title, unified_diff, max_files=max_files)

    def enqueue_github(self, owner: str, repo: str, pr_number: int, *, max_files: int = 25):
        if not self._enabled:
            raise RuntimeError("Task queue is disabled")
        return enqueue_github_review(owner, repo, pr_number, max_files=max_files)


class ReviewService:
    """Coordinates review execution across queue and inline flows."""

    def __init__(
        self,
        *,
        context_config: ContextConfig,
        enable_context_indexing: bool,
        queue: Optional[ReviewQueue] = None,
    ) -> None:
        self._context_config = context_config
        self._enable_context_indexing = enable_context_indexing
        self._queue = queue if queue and queue.enabled else None

    async def review_diff(self, pr_title: str, unified_diff: str, *, max_files: int = 25) -> ReviewOutcome:
        queue_error: Optional[str] = None
        if self._queue:
            try:
                task = self._queue.enqueue_diff(pr_title, unified_diff, max_files=max_files)
                return ReviewOutcome(task_id=task.id)
            except RuntimeError as exc:
                queue_error = str(exc)

        result = await review_pr_async(
            pr_title,
            unified_diff,
            max_files=max_files,
        )
        return ReviewOutcome(result=result, queue_error=queue_error)

    async def review_github(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 25,
    ) -> ReviewOutcome:
        queue_error: Optional[str] = None
        if self._queue:
            try:
                task = self._queue.enqueue_github(owner, repo, pr_number, max_files=max_files)
                return ReviewOutcome(task_id=task.id)
            except RuntimeError as exc:
                queue_error = str(exc)

        details = get_pr_details(owner, repo, pr_number)
        context_service = self._build_context_service(
            owner=owner,
            repo=repo,
            base_sha=details.get("base_sha"),
            pr_title=details.get("title"),
        )
        result = await review_pr_async(
            details.get("title", f"PR #{pr_number}"),
            details.get("diff", ""),
            max_files=max_files,
            context_service=context_service,
        )
        result["pr"] = {
            "owner": owner,
            "repo": repo,
            "number": pr_number,
            "title": details.get("title"),
        }
        metadata = {"head_sha": details.get("head_sha")}
        return ReviewOutcome(result=result, queue_error=queue_error, metadata=metadata)

    async def process_pull_request_event(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        *,
        max_files: int = 25,
    ) -> ReviewOutcome:
        outcome = await self.review_github(owner, repo, pr_number, max_files=max_files)
        if outcome.queued:
            return outcome

        result = outcome.result or {}
        per_file_diffs = result.get("per_file_diffs", {})
        annotations = build_github_annotations(result, per_file_diffs)
        summary_md = build_check_summary_markdown(result)
        conclusion = result_to_check_conclusion(result)
        create_or_update_check_run(owner, repo, head_sha, conclusion, summary_md, annotations)
        return outcome

    def _build_context_service(
        self,
        *,
        owner: str,
        repo: str,
        base_sha: Optional[str],
        pr_title: Optional[str],
    ) -> Optional[RepositoryContextService]:
        if not self._enable_context_indexing or not base_sha:
            return None
        return RepositoryContextService(
            owner=owner,
            repo=repo,
            base_sha=base_sha,
            pr_title=pr_title or "",
            config=self._context_config,
        )
