from __future__ import annotations
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.settings import OPENAI_MODEL, ENABLE_CONTEXT_INDEXING, ENABLE_TASK_QUEUE
from src.schemas import GithubReviewRequest, ReviewRequest
from src.github import get_pr_details, create_or_update_check_run
from src.reviewer import review_pr, review_pr_async
from src.context import ContextConfig, RepositoryContextService
from src.security import verify_github_signature
from src.utils import (
    build_github_annotations,
    build_check_summary_markdown,
    result_to_check_conclusion,
)
from src.task_queue import enqueue_diff_review, enqueue_github_review

CONTEXT_CONFIG = ContextConfig.from_settings()

app = FastAPI(title="MergeWise — Intelligent Pull Request Reviewer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

@app.get("/health")
def health():
    return {"ok": True, "model": OPENAI_MODEL}

@app.post("/review")
async def review(req: ReviewRequest):
    queue_error = None
    if ENABLE_TASK_QUEUE:
        try:
            job = enqueue_diff_review(req.pr_title, req.unified_diff)
            return {"status": "queued", "task_id": job.id}
        except RuntimeError as exc:
            queue_error = str(exc)

    result = await review_pr_async(req.pr_title, req.unified_diff)
    if queue_error:
        result["queue_error"] = queue_error
    return result

@app.post("/review/github")
async def review_github(req: GithubReviewRequest):
    queue_error = None
    if ENABLE_TASK_QUEUE:
        try:
            job = enqueue_github_review(
                req.owner,
                req.repo,
                req.pr_number,
                max_files=req.max_files,
            )
            return {"status": "queued", "task_id": job.id}
        except RuntimeError as exc:
            queue_error = str(exc)

    details = get_pr_details(req.owner, req.repo, req.pr_number)
    context_service = None
    if ENABLE_CONTEXT_INDEXING and details.get("base_sha"):
        context_service = RepositoryContextService(
            owner=req.owner,
            repo=req.repo,
            base_sha=details["base_sha"],
            pr_title=details["title"],
            config=CONTEXT_CONFIG,
        )
    result = await review_pr_async(
        details["title"],
        details["diff"],
        max_files=req.max_files,
        context_service=context_service,
    )
    result["pr"] = {
        "owner": req.owner,
        "repo": req.repo,
        "number": req.pr_number,
        "title": details["title"],
    }
    if queue_error:
        result["queue_error"] = queue_error
    return result

@app.post("/github/webhook")
async def github_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not verify_github_signature(raw, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event")
    payload = json.loads(raw.decode("utf-8"))

    if event == "pull_request" and payload.get("action") in {"opened", "synchronize", "reopened"}:
        repo = payload["repository"]["name"]
        owner = payload["repository"]["owner"]["login"]
        pr_number = payload["number"]
        head_sha = payload["pull_request"]["head"]["sha"]  # needed for check run

        queue_fallback_error = None
        if ENABLE_TASK_QUEUE:
            try:
                enqueue_github_review(owner, repo, pr_number, max_files=25)
                return {"ok": True}
            except RuntimeError as exc:
                queue_fallback_error = str(exc)

            details = get_pr_details(owner, repo, pr_number)
            context_service = None
            if ENABLE_CONTEXT_INDEXING and details.get("base_sha"):
                context_service = RepositoryContextService(
                    owner=owner,
                    repo=repo,
                    base_sha=details["base_sha"],
                    pr_title=details["title"],
                    config=CONTEXT_CONFIG,
                )
            result = await review_pr_async(
                details["title"],
                details["diff"],
                max_files=25,
                context_service=context_service,
            )

            per_file_diffs = result.get("per_file_diffs", {})
            annotations = build_github_annotations(result, per_file_diffs)
            summary_md = build_check_summary_markdown(result)
            conclusion = result_to_check_conclusion(result)
            create_or_update_check_run(owner, repo, head_sha, conclusion, summary_md, annotations)
            if ENABLE_TASK_QUEUE and queue_fallback_error:
                result.setdefault("queue_error", queue_fallback_error)
    return {"ok": True}
