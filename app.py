from __future__ import annotations
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.settings import OPENAI_MODEL
from src.types import GithubReviewRequest, ReviewRequest
from src.github import get_pr_title_and_diff, post_pr_comment
from src.reviewer import review_pr
from src.security import verify_github_signature
from src.utils import render_markdown_comment

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
def review(req: ReviewRequest):
    return review_pr(req.pr_title, req.unified_diff)

@app.post("/review/github")
def review_github(req: GithubReviewRequest):
    pr_title, diff = get_pr_title_and_diff(req.owner, req.repo, req.pr_number)
    result = review_pr(pr_title, diff, max_files=req.max_files)
    result["pr"] = {"owner": req.owner, "repo": req.repo, "number": req.pr_number, "title": pr_title}
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
        title, diff = get_pr_title_and_diff(owner, repo, pr_number)
        result = review_pr(title, diff, max_files=25)
        comment = render_markdown_comment(result)
        post_pr_comment(owner, repo, pr_number, comment)

    return {"ok": True}