from __future__ import annotations
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.context import ContextConfig
from src.schemas import GithubReviewRequest, ReviewRequest
from src.security import verify_github_signature
from src.services import ReviewService, ReviewQueue
from src.settings import OPENAI_MODEL, ENABLE_CONTEXT_INDEXING, ENABLE_TASK_QUEUE

CONTEXT_CONFIG = ContextConfig.from_settings()
REVIEW_QUEUE = ReviewQueue(enabled=ENABLE_TASK_QUEUE)
review_service = ReviewService(
    context_config=CONTEXT_CONFIG,
    enable_context_indexing=ENABLE_CONTEXT_INDEXING,
    queue=REVIEW_QUEUE if ENABLE_TASK_QUEUE else None,
)

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
    outcome = await review_service.review_diff(req.pr_title, req.unified_diff)
    if outcome.queued:
        return {"status": "queued", "task_id": outcome.task_id}

    result = outcome.result or {}
    if outcome.queue_error:
        result["queue_error"] = outcome.queue_error
    return result

@app.post("/review/github")
async def review_github(req: GithubReviewRequest):
    outcome = await review_service.review_github(
        req.owner, req.repo, req.pr_number, max_files=req.max_files
    )
    if outcome.queued:
        return {"status": "queued", "task_id": outcome.task_id}

    result = outcome.result or {}
    if outcome.queue_error:
        result["queue_error"] = outcome.queue_error
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

        outcome = await review_service.process_pull_request_event(
            owner,
            repo,
            pr_number,
            head_sha,
            max_files=25,
        )
        if outcome.queue_error:
            return {"ok": True, "queue_error": outcome.queue_error}
    return {"ok": True}
