from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()  # load .env locally; on Render/Railway envs are injected

# App config
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Webhook secret for HMAC verification; leave empty to skip in local dev
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# GitHub App
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()

# Private key can be pasted with real newlines or \n-escaped single line
_pem_env = os.getenv("GITHUB_APP_PRIVATE_KEY_PEM", "")
if "\\n" in _pem_env and "\n" not in _pem_env:
    _pem_env = _pem_env.replace("\\n", "\n")
GITHUB_APP_PRIVATE_KEY_PEM = _pem_env

# Optional personal token (rarely needed; App installation token is preferred)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# GitHub API root (override for GHES)
GITHUB_API_BASE = os.getenv("GITHUB_API", "https://api.github.com")
