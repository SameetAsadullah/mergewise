from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from .security import get_installation_token
from .settings import GITHUB_API_BASE

_USER_AGENT = "MergeWise/1.0"


@dataclass
class PullRequestDetails:
    title: str
    diff: str
    base_ref: Optional[str]
    base_sha: Optional[str]
    head_ref: Optional[str]
    head_sha: Optional[str]
    number: int


class GitHubClient:
    """Minimal GitHub REST client for PR metadata and check runs."""

    def __init__(self, owner: str, repo: str, api_base: str = GITHUB_API_BASE) -> None:
        self.owner = owner
        self.repo = repo
        self.api_base = api_base.rstrip("/")

    # ------------------------------------------------------------------
    def get_pull_request_details(self, pr_number: int) -> PullRequestDetails:
        token = self._installation_token()
        pr_json = self._get_pull_request(pr_number, token)
        diff = self._get_pull_request_diff(pr_number, token)
        base = pr_json.get("base") or {}
        head = pr_json.get("head") or {}
        return PullRequestDetails(
            title=pr_json.get("title") or f"PR #{pr_number}",
            diff=diff,
            base_ref=base.get("ref"),
            base_sha=base.get("sha"),
            head_ref=head.get("ref"),
            head_sha=head.get("sha"),
            number=pr_number,
        )

    def create_or_update_check_run(
        self,
        *,
        head_sha: str,
        conclusion: str,
        summary_md: str,
        annotations: List[Dict[str, Any]],
        name: str = "PR Review",
    ) -> None:
        token = self._installation_token()
        check = self._create_completed_check(
            token=token,
            head_sha=head_sha,
            conclusion=conclusion,
            summary_md=summary_md,
            name=name,
        )
        self._append_annotations(token=token, check_id=check["id"], summary_md=summary_md, annotations=annotations)

    # ------------------------------------------------------------------
    def _get_pull_request(self, pr_number: int, token: str) -> Dict[str, Any]:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        response = requests.get(url, headers=self._headers("application/vnd.github+json", token), timeout=30)
        response.raise_for_status()
        return response.json()

    def _get_pull_request_diff(self, pr_number: int, token: str) -> str:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        response = requests.get(url, headers=self._headers("application/vnd.github.v3.diff", token), timeout=60)
        response.raise_for_status()
        return response.text

    def _create_completed_check(
        self,
        *,
        token: str,
        head_sha: str,
        conclusion: str,
        summary_md: str,
        name: str,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/check-runs"
        payload = {
            "name": name,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": "MergeWise findings",
                "summary": summary_md[:65535],
                "annotations": [],
            },
        }
        response = requests.post(
            url,
            headers=self._headers("application/vnd.github+json", token),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _append_annotations(
        self,
        *,
        token: str,
        check_id: int,
        summary_md: str,
        annotations: List[Dict[str, Any]],
    ) -> None:
        batch_size = 50
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/check-runs/{check_id}"
        for index in range(0, len(annotations), batch_size):
            batch = annotations[index : index + batch_size]
            payload = {
                "output": {
                    "title": "MergeWise findings",
                    "summary": summary_md[:65535],
                    "annotations": batch,
                }
            }
            response = requests.patch(
                url,
                headers=self._headers("application/vnd.github+json", token),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

    def _installation_token(self) -> str:
        return get_installation_token(self.owner, self.repo)

    @staticmethod
    def _headers(accept: str, token: Optional[str] = None) -> Dict[str, str]:
        headers = {"User-Agent": _USER_AGENT, "Accept": accept}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


# ---------------------------------------------------------------------------
# Backwards-compatible functional helpers


def get_pr_details(owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
    client = GitHubClient(owner, repo)
    details = client.get_pull_request_details(pr_number)
    return {
        "title": details.title,
        "diff": details.diff,
        "base_ref": details.base_ref,
        "base_sha": details.base_sha,
        "head_ref": details.head_ref,
        "head_sha": details.head_sha,
        "number": details.number,
    }


def create_or_update_check_run(
    owner: str,
    repo: str,
    head_sha: str,
    conclusion: str,
    summary_md: str,
    annotations: List[Dict[str, Any]],
    name: str = "PR Review",
) -> None:
    client = GitHubClient(owner, repo)
    client.create_or_update_check_run(
        head_sha=head_sha,
        conclusion=conclusion,
        summary_md=summary_md,
        annotations=annotations,
        name=name,
    )
