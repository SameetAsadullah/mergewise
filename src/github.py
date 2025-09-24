from __future__ import annotations
import requests

from typing import Tuple, List, Dict, Any

from .settings import GITHUB_API_BASE
from .security import get_installation_token

def _headers(accept: str, token: str | None = None) -> dict:
    h = {"User-Agent": "PullSense/1.0", "Accept": accept}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _get_pr_json(owner: str, repo: str, pr_number: int, token: str | None = None) -> Dict[str, Any]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(url, headers=_headers("application/vnd.github+json", token), timeout=30)
    r.raise_for_status()
    return r.json()


def get_pr_title(owner: str, repo: str, pr_number: int, token: str | None = None) -> str:
    data = _get_pr_json(owner, repo, pr_number, token)
    return data.get("title") or f"PR #{pr_number}"

def get_pr_diff(owner: str, repo: str, pr_number: int, token: str | None = None) -> str:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(url, headers=_headers("application/vnd.github.v3.diff", token), timeout=60)
    r.raise_for_status()
    return r.text

def get_pr_title_and_diff(owner: str, repo: str, pr_number: int) -> Tuple[str, str]:
    details = get_pr_details(owner, repo, pr_number)
    return details["title"], details["diff"]


def get_pr_details(owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
    token = get_installation_token(owner, repo)
    pr_json = _get_pr_json(owner, repo, pr_number, token)
    diff = get_pr_diff(owner, repo, pr_number, token)
    base = pr_json.get("base") or {}
    head = pr_json.get("head") or {}
    return {
        "title": pr_json.get("title") or f"PR #{pr_number}",
        "diff": diff,
        "base_ref": base.get("ref"),
        "base_sha": base.get("sha"),
        "head_ref": head.get("ref"),
        "head_sha": head.get("sha"),
        "number": pr_number,
    }

def create_or_update_check_run(
    owner: str,
    repo: str,
    head_sha: str,
    conclusion: str,
    summary_md: str,
    annotations: List[Dict[str, Any]],
    name: str = "PR Review"
) -> None:
    """
    Create a completed Check Run with summary + annotations.
    Note: GitHub allows max 50 annotations per request; we batch if needed.
    """
    token = get_installation_token(owner, repo)

    # Create the check run first (completed or queued)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    # Start as completed with summary (no annotations yet)
    payload = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,  # "neutral" | "failure"
        "output": {
            "title": "MergeWise findings",
            "summary": summary_md[:65535],
            "annotations": []  # add separately in batches
        }
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    check = r.json()
    check_id = check["id"]

    # Batch annotations (max 50 per request)
    batch_size = 50
    for i in range(0, len(annotations), batch_size):
        batch = annotations[i:i+batch_size]
        u = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs/{check_id}"
        payload = {
            "output": {
                "title": "MergeWise findings",
                "summary": summary_md[:65535],
                "annotations": batch
            }
        }
        r = requests.patch(u, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
