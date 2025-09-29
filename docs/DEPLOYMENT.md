# Deployment Guide

This guide outlines the recommended steps for deploying MergeWise in production.

## Prerequisites
- Python 3.11 runtime (or Docker).
- OpenAI API key with access to the configured models.
- GitHub App credentials: App ID, private key, webhook secret.
- Persistent storage for FAISS indexes (disk or mount).
- HTTPS endpoint for webhook delivery.

## Environment Variables
MergeWise reads configuration from environment variables (via `python-dotenv`). Minimum set:
```
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
GITHUB_APP_ID=<github-app-id>
GITHUB_APP_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=<your-secret>
ENABLE_CONTEXT_INDEXING=true
CONTEXT_INDEX_DIR=/data/context-indexes
ENABLE_TASK_QUEUE=true                # enable Celery/Redis offload
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```
Optional overrides (see `src/settings.py` / `src/context/config.py`): chunk sizes, retrieval limits, reranker toggle, API base for GitHub Enterprise.

## Docker Deployment
1. Build the image:
   ```bash
   docker build -t mergewise .
   ```
2. Run the API container:
   ```bash
   docker run -d \
     --name mergewise \
     -p 8000:8000 \
     --env-file .env \
     -v $(pwd)/data:/data \
     mergewise web
   ```
   Mount `/data` (or your chosen directory) if you want persistent FAISS indexes across restarts.
3. (Optional) Run a worker container to consume queued jobs:
   ```bash
   docker run -d \
     --name mergewise-worker \
     --env-file .env \
     mergewise worker
   ```
4. Verify health:
   ```bash
   curl http://localhost:8000/health
   ```

## Bare-Metal / VM Deployment
1. Create a virtualenv and install requirements:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
2. Export environment variables or create a `.env` file.
3. If `ENABLE_TASK_QUEUE=1`, start Redis and a Celery worker (e.g., `celery -A src.task_queue:celery_app worker --loglevel=info`).
4. Run the web API with uvicorn or a process manager:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```
   For production, wrap with `gunicorn` or `uvicorn` workers behind a reverse proxy (NGINX/Caddy).
5. Ensure the service runs under a user with access to the FAISS index directory.

## GitHub App Setup
1. In GitHub → Settings → Developer settings → GitHub Apps, create an app with:
   - **Repository permissions**: Checks (Read & Write), Pull requests (Read).
   - **Events**: `pull_request` (required).
   - **Webhook URL**: `https://<your-domain>/github/webhook`.
   - **Secret**: match `GITHUB_WEBHOOK_SECRET`.
2. Install the app on your repository or organization.
3. Provide the app credentials/environment variables to the MergeWise service.

## High Availability Considerations
- Use a managed database or remote storage for the FAISS index if disks are ephemeral.
- Run multiple app instances behind a load balancer; ensure the GitHub webhook URL points to a shared ingress.
- Enable logging/monitoring (e.g., send logs to CloudWatch, Stackdriver, or Loki) with request IDs and timing metrics.

## Operational Tips
- Monitor token usage and latency by instrumenting the OpenAI client or wrapping it with logging.
- Configure a retry/backoff strategy for GitHub API rate limits (the current client raises on failure; add custom retry middleware if necessary).
- Rotate the GitHub private key securely; update env vars and redeploy when keys change.
- Back up `CONTEXT_INDEX_DIR` regularly if rebuild cost is high.

## Testing in Staging
- Install the GitHub App on a staging repo first.
- Set `ENABLE_CONTEXT_INDEXING=false` to disable heavy retrieval while sanity-checking LLM responses.
- Use `pytest` to run the full test suite before deploying (`PYTHONPATH=. pytest`).

## Troubleshooting
- **Webhook returns 401**: ensure `GITHUB_WEBHOOK_SECRET` matches GitHub app settings.
- **Check run remains neutral**: confirm at least one finding has `severity` = `BLOCKER`; inspect the JSON payload logged by the reviewer.
- **FAISS errors**: the Docker image installs OpenBLAS; if running on Alpine/musl, compile FAISS with compatible BLAS.
- **Slow reviews**: reduce `CONTEXT_MAX_FILES`, lower `CONTEXT_TOP_K`, or run reviews asynchronously.

## Future Enhancements
- Deploy a background worker (Celery, RQ) to apply suggested patches triggered by check-run actions.
- Add metrics collection (Prometheus exporters) for request counts, LLM latency, token usage.
- Introduce feature flags via environment variables for experimental prompt variants.

Keep this guide updated as deployment tooling evolves.
