from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

# App config
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# Webhook secret for HMAC verification; leave empty to skip in local dev
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# GitHub App
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()

# Private key can be pasted with real newlines or \n-escaped single line
_pem_env = os.getenv("GITHUB_APP_PRIVATE_KEY_PEM", "")
if "\\n" in _pem_env and "\n" not in _pem_env:
    _pem_env = _pem_env.replace("\\n", "\n")
GITHUB_APP_PRIVATE_KEY_PEM = _pem_env

# GitHub API root (override for GHES)
GITHUB_API_BASE = os.getenv("GITHUB_API", "https://api.github.com")

# Context-aware review configuration
ENABLE_CONTEXT_INDEXING = os.getenv("ENABLE_CONTEXT_INDEXING", "true").lower() not in {"0", "false", "no"}
CONTEXT_INDEX_DIR = os.getenv("CONTEXT_INDEX_DIR", "data/context-indexes")
CONTEXT_MAX_CHARS_PER_CHUNK = int(os.getenv("CONTEXT_MAX_CHARS_PER_CHUNK", "1200"))
CONTEXT_MAX_FILE_BYTES = int(os.getenv("CONTEXT_MAX_FILE_BYTES", "80000"))
CONTEXT_MAX_FILES = int(os.getenv("CONTEXT_MAX_FILES", "200"))
CONTEXT_TOP_K = int(os.getenv("CONTEXT_TOP_K", "4"))
CONTEXT_RETRIEVAL_CANDIDATES = int(os.getenv("CONTEXT_RETRIEVAL_CANDIDATES", "12"))
CONTEXT_ENABLE_RERANKER = os.getenv("CONTEXT_ENABLE_RERANKER", "true").lower() not in {"0", "false", "no"}
CONTEXT_RERANK_MODEL = os.getenv("CONTEXT_RERANK_MODEL", OPENAI_MODEL)
CONTEXT_RERANK_MAX_CHARS = int(os.getenv("CONTEXT_RERANK_MAX_CHARS", "900"))

# Task queue configuration
ENABLE_TASK_QUEUE = os.getenv("ENABLE_TASK_QUEUE", "false").lower() in {"1", "true", "yes"}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
