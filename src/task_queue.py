from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from celery import Celery
    from celery.exceptions import CeleryError
    from kombu.exceptions import OperationalError as KombuOperationalError
except ImportError as exc:  # pragma: no cover - optional dependency
    Celery = None  # type: ignore
    CeleryError = Exception  # type: ignore
    KombuOperationalError = Exception  # type: ignore
    _celery_import_error = exc
else:
    _celery_import_error = None

from .context import ContextConfig, RepositoryContextService
from .github import create_or_update_check_run, get_pr_details
from .reviewer import review_pr
from .settings import (
    CELERY_BROKER_URL,
    CELERY_RESULT_BACKEND,
    ENABLE_CONTEXT_INDEXING,
)
from .utils import (
    build_check_summary_markdown,
    build_github_annotations,
    result_to_check_conclusion,
)

logger = logging.getLogger(__name__)

if Celery is None:  # pragma: no cover - optional dependency fallback
    celery_app = None

    def _raise_missing_celery() -> None:
        raise RuntimeError(
            "Celery is not installed. Install 'celery[redis]' to use the background task queue."
        ) from _celery_import_error

    def enqueue_diff_review(
        pr_title: str,
        unified_diff: str,
        *,
        max_files: int = 25,
        context_payload: Optional[Dict[str, Any]] = None,
    ):
        _raise_missing_celery()

    def enqueue_github_review(
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 25,
    ):
        _raise_missing_celery()

    __all__ = ["celery_app", "enqueue_diff_review", "enqueue_github_review"]

else:
    celery_app = Celery(
        "mergewise",
        broker=CELERY_BROKER_URL,
        backend=CELERY_RESULT_BACKEND,
    )
    celery_app.conf.update(
        task_default_queue="reviews",
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
    )

    def _build_context_service(payload: Optional[Dict[str, Any]]) -> Optional[RepositoryContextService]:
        if not payload:
            return None
        config = ContextConfig.from_settings()
        return RepositoryContextService(
            owner=payload["owner"],
            repo=payload["repo"],
            base_sha=payload["base_sha"],
            pr_title=payload["pr_title"],
            config=config,
        )

    @celery_app.task(name="mergewise.review.diff")
    def review_diff_task(
        pr_title: str,
        unified_diff: str,
        *,
        max_files: int = 25,
        context_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Celery task to review an arbitrary diff payload."""
        context_service = _build_context_service(context_payload)
        result = review_pr(
            pr_title,
            unified_diff,
            max_files=max_files,
            context_service=context_service,
        )
        return result

    @celery_app.task(name="mergewise.review.github")
    def review_github_task(
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 25,
    ) -> Dict[str, Any]:
        """Celery task to fetch PR details, run review, and update GitHub check runs."""
        details = get_pr_details(owner, repo, pr_number)

        context_payload: Optional[Dict[str, Any]] = None
        if ENABLE_CONTEXT_INDEXING and details.get("base_sha"):
            context_payload = {
                "owner": owner,
                "repo": repo,
                "base_sha": details["base_sha"],
                "pr_title": details["title"],
            }

        context_service = _build_context_service(context_payload)

        result = review_pr(
            details["title"],
            details["diff"],
            max_files=max_files,
            context_service=context_service,
        )
        result["pr"] = {
            "owner": owner,
            "repo": repo,
            "number": pr_number,
            "title": details["title"],
        }

        head_sha = details.get("head_sha")
        if head_sha:
            per_file_diffs = result.get("per_file_diffs", {})
            annotations = build_github_annotations(result, per_file_diffs)
            summary_md = build_check_summary_markdown(result)
            conclusion = result_to_check_conclusion(result)
            try:
                create_or_update_check_run(
                    owner,
                    repo,
                    head_sha,
                    conclusion,
                    summary_md,
                    annotations,
                )
            except Exception:  # pragma: no cover - best effort update
                logger.exception(
                    "Failed to create or update check run for %s/%s #%s", owner, repo, pr_number
                )

        return result

    def _enqueue_with_logging(task, *, kwargs: Dict[str, Any]):
        try:
            return task.apply_async(kwargs=kwargs)
        except KombuOperationalError as exc:
            logger.exception("Celery broker connection failed when enqueuing task")
            raise RuntimeError(
                "Failed to enqueue review task: unable to reach Celery broker (check Redis)."
            ) from exc
        except CeleryError as exc:
            logger.exception("Celery internal error while enqueuing task")
            raise RuntimeError("Failed to enqueue review task due to Celery error.") from exc

    def enqueue_diff_review(
        pr_title: str,
        unified_diff: str,
        *,
        max_files: int = 25,
        context_payload: Optional[Dict[str, Any]] = None,
    ):
        """Helper to enqueue a diff review task and return AsyncResult."""
        return _enqueue_with_logging(
            review_diff_task,
            kwargs={
                "pr_title": pr_title,
                "unified_diff": unified_diff,
                "max_files": max_files,
                "context_payload": context_payload,
            },
        )

    def enqueue_github_review(
        owner: str,
        repo: str,
        pr_number: int,
        *,
        max_files: int = 25,
    ):
        """Helper to enqueue a GitHub review task and return AsyncResult."""
        return _enqueue_with_logging(
            review_github_task,
            kwargs={
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "max_files": max_files,
            },
        )

    __all__ = [
        "celery_app",
        "enqueue_diff_review",
        "enqueue_github_review",
    ]
