from __future__ import annotations
import hmac, hashlib, time, requests
from typing import Optional
import jwt  # PyJWT

from .settings import (
    GITHUB_WEBHOOK_SECRET, GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PEM, GITHUB_API_BASE
)

def verify_github_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Verify X-Hub-Signature-256 using the shared webhook secret."""
    if not GITHUB_WEBHOOK_SECRET:
        return True
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=raw_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature_header or "")

def build_app_jwt() -> str:
    """Sign a short-lived JWT as the GitHub App."""
    if not (GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PEM):
        raise RuntimeError("Missing GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY_PEM")
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": GITHUB_APP_ID}
    return jwt.encode(payload, GITHUB_APP_PRIVATE_KEY_PEM, algorithm="RS256")

def get_installation_token(owner: str, repo: str) -> str:
    """Exchange App JWT for an installation access token scoped to the repo."""
    app_jwt = build_app_jwt()
    # 1) find installation
    r = requests.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/installation",
        headers={"Authorization": f"Bearer {app_jwt}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    r.raise_for_status()
    inst_id = r.json()["id"]
    # 2) create token
    r = requests.post(
        f"{GITHUB_API_BASE}/app/installations/{inst_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["token"]
