# Task Queue Guide

This document explains how MergeWise uses Celery and Redis to offload long running reviews, and how to operate the queue in local and production environments.

## Overview
- **ReviewService** (app layer) decides whether to enqueue a review or run it inline.
- **ReviewQueue** wraps Celery helpers. When `ENABLE_TASK_QUEUE=1`, API routes enqueue jobs and return a `task_id` immediately.
- **Celery worker** consumes jobs via Redis, runs the review synchronously, and updates GitHub check runs if the task was triggered by a webhook.

### Queue-enabled vs Inline
| Setting | Behaviour |
|---------|-----------|
| `ENABLE_TASK_QUEUE=0` | Reviews run inline within the FastAPI request. Good for local dev or low traffic. |
| `ENABLE_TASK_QUEUE=1` | Requests enqueue tasks and return right away. Requires Redis + Celery worker. |

If the queue is enabled but Redis/Celery are unavailable, the API logs the error, runs the review inline, and attaches `queue_error` in the response. This keeps operations resilient while highlighting the configuration issue.

## Local Development
1. Start Redis:
   ```bash
   docker run --rm -p 6379:6379 redis:7-alpine
   ```
2. Export queue settings (or add to `.env`):
   ```bash
   export ENABLE_TASK_QUEUE=1
   export CELERY_BROKER_URL=redis://localhost:6379/0
   export CELERY_RESULT_BACKEND=redis://localhost:6379/0
   ```
3. Run the API:
   ```bash
   uvicorn app:app --reload
   ```
4. Start a worker in a second shell:
   ```bash
   celery -A src.task_queue:celery_app worker --loglevel=info
   ```

## Docker / Compose Pattern
- Build the image once (`docker build -t mergewise .`).
- Start web and worker containers:
  ```bash
  docker run --rm --env-file .env mergewise web
  docker run --rm --env-file .env mergewise worker
  ```
- Provide a Redis URL reachable by both containers (e.g., `redis://redis:6379/0` when orchestrated with compose).

## Cloud Deployment Tips
- Managed Redis services (Upstash, Redis Cloud, Railway) work well for small workloads.
- When deploying to platforms like Render or Railway:
  - Web service start command: `./docker-entrypoint.sh web`
  - Background worker start command: `./docker-entrypoint.sh worker`
  - Share the same `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` env vars.
- Lock down Redis access with passwords/TLS; store secret URLs in the platform’s env var manager.

## Monitoring & Troubleshooting
- **No jobs processed**: ensure the worker logs show it connected to Redis and the broker URL matches the API.
- **RuntimeError: unable to reach Celery broker**: Redis is unreachable. Verify network/firewall, credentials, and SSL requirements.
- **High latency**: scale worker concurrency (`celery ... --concurrency=4`) or add more worker instances.
- **Stuck jobs**: inspect Redis keys for Celery queues (`celery inspect active`) and clear old results if needed (`celery purge`).

## Advanced Configuration
- Customize Celery options (prefetch, serialization) in `src/task_queue.py`.
- Update retry/fallback policy by extending `ReviewQueue` or catching more exceptions in `_enqueue_with_logging`.
- To schedule periodic reviews (metrics, audits), run `docker-entrypoint.sh beat` or add a separate Celery beat deployment.

## Migration Checklist
1. Provision Redis (managed or self-hosted) and obtain connection URL.
2. Update `.env` with queue-related variables.
3. Deploy web service and worker using the same code revision.
4. Confirm `/health` works and the worker logs show `ready` status.
5. Trigger a `/review` request; check that the response contains a `task_id` and that the worker processes the task.

Following this guide ensures the queue stays reliable while keeping MergeWise responsive under heavy review workloads.
