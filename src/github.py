from __future__ import annotations
import requests
from typing import Tuple

from .settings import GITHUB_API_BASE
from .security import get_installation_token

def _headers(accept: str, token: str | None = None) -> dict:
    h = {"User-Agent": "PullSense/1.0", "Accept": accept}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def get_pr_title(owner: str, repo: str, pr_number: int, token: str | None = None) -> str:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(url, headers=_headers("application/vnd.github+json", token), timeout=30)
    r.raise_for_status()
    return r.json().get("title") or f"PR #{pr_number}"

def get_pr_diff(owner: str, repo: str, pr_number: int, token: str | None = None) -> str:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(url, headers=_headers("application/vnd.github.v3.diff", token), timeout=60)
    r.raise_for_status()
    return r.text

def get_pr_title_and_diff(owner: str, repo: str, pr_number: int) -> Tuple[str, str]:
    token = get_installation_token(owner, repo)
    title = get_pr_title(owner, repo, pr_number, token)
    diff = get_pr_diff(owner, repo, pr_number, token)
    return title, diff

def post_pr_comment(owner: str, repo: str, pr_number: int, markdown: str) -> None:
    token = get_installation_token(owner, repo)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    r = requests.post(
        url,
        headers=_headers("application/vnd.github+json", token),
        json={"body": markdown},
        timeout=30,
    )
    r.raise_for_status()
