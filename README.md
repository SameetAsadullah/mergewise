# MergeWise

MergeWise is a production-ready AI assistant that performs context-aware code reviews for GitHub pull requests. It ingests repository documentation and code, stores embeddings in FAISS, retrieves the most relevant context for each diff, and asks an LLM to deliver structured findings. The service posts GitHub Check annotations with rationale and fix-ready snippets so contributors can resolve issues quickly.

## Why It Stands Out
- **Context-aware reviews** – fetches repo docs/config with OpenAI embeddings + FAISS and re-ranks with an LLM to supply precise context to each file review.
- **Structured, actionable output** – every finding includes severity (`BLOCKER`, `WARNING`, `NIT`), rationale, and a fix suggestion rendered in the check annotation.
- **GitHub-native UX** – push-button GitHub App integration: health endpoint, `/review` API, webhook handling, and check runs with annotations.
- **Modular architecture** – clean separation between ingestion (`context/`), review engine, GitHub client, FastAPI transport, and utilities.
- **Tested & reproducible** – extensive pytest suite with deterministic stubs (OpenAI, GitHub, requests); all tests pass with `pytest`.

## Project Structure
```
├─ app.py                 # FastAPI entrypoint & routes
├─ src/
│  ├─ context/            # Context indexing + retrieval (config, chunking, FAISS store, reranker, service)
│  ├─ github.py           # GitHub REST client + helpers
│  ├─ reviewer.py         # ReviewEngine (diff parsing, LLM prompts, aggregation)
│  ├─ services/           # Application-level orchestration (ReviewService, queue helpers)
│  ├─ schemas.py          # Pydantic request bodies
│  ├─ utils.py            # Check-run annotation helpers, diff utilities
│  └─ ...                 # security, settings, etc.
├─ docs/                  # Architecture & deployment guides
├─ requirements.txt       # Runtime dependencies
├─ Dockerfile             # Containerized deployment
├─ tests/                 # pytest suite with fixtures + unit/integration tests
└─ README.md              # Project documentation
```

## Getting Started
### 1. Install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment
Create a `.env` (read by `python-dotenv`) with your credentials:
```
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
GITHUB_APP_ID=<github-app-id>
GITHUB_APP_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=<optional>
ENABLE_TASK_QUEUE=0               # set to 1 to offload reviews to Celery + Redis
LOG_FILE=logs/mergewise.log       # optional: customize log destination
LOG_LEVEL=INFO
``` 
Other tunables (chunk size, FAISS index dir, reranker model, etc.) live in `src/settings.py` / `src/context/config.py`.

### 3. Run the API locally
```bash
source venv/bin/activate
uvicorn app:app --reload
```

### Optional: Background task queue (Celery + Redis)
The FastAPI routes run reviews inline by default. To keep the API responsive under heavy load, enable the Celery worker:

1. Start Redis (or point `CELERY_BROKER_URL` to an existing instance):
   ```bash
   docker run --rm -p 6379:6379 redis:7-alpine
   ```
2. Export queue settings (or add them to `.env`):
   ```bash
   export ENABLE_TASK_QUEUE=1
   export CELERY_BROKER_URL=redis://localhost:6379/0
   export CELERY_RESULT_BACKEND=redis://localhost:6379/0
   ```
3. Run the API as usual, then start the worker in another terminal:
   ```bash
   source venv/bin/activate
   celery -A src.task_queue:celery_app worker --loglevel=info
   ```
When `ENABLE_TASK_QUEUE=1`, `/review`, `/review/github`, and webhook calls enqueue jobs and return immediately with a `task_id`. Workers fetch PR metadata, run the review engine, and update GitHub check runs.

### Optional: Run in Docker
```bash
docker build -t mergewise .
docker run --rm -p 8000:8000 --env-file .env mergewise web
# visit http://localhost:8000/health
```

Run an additional worker container alongside the web API:
```bash
docker run --rm --env-file .env mergewise worker
```

Useful endpoints:
- `GET /health` – returns app status + active model.
- `POST /review` – manual review of a diff (`ReviewRequest`).
- `POST /review/github` – review by owner/repo/pr number (`GithubReviewRequest`).
- `POST /github/webhook` – GitHub webhook handler for PR events.

## GitHub App Integration
1. Create a GitHub App with **Repository** permissions for Checks (read/write) and Pull Requests (read). Install it on your repo.
2. Configure the webhook URL to point to `/github/webhook` and use the same secret as `GITHUB_WEBHOOK_SECRET`.
3. Deploy the FastAPI service (e.g., Fly.io, Railway, AWS) with the same env vars. On PR open/sync/reopen, the webhook triggers a review, generates findings, and updates a GitHub Check Run with annotations and fix snippets.

## How Reviews Work
1. **Request orchestration** – `ReviewService` decides whether to enqueue the review on Celery/Redis or execute inline, and prepares context services as needed.
2. **Diff parsing** – `DiffParser` splits unified diffs into file chunks.
3. **Context retrieval** – `RepositoryContextService` indexes repository blobs with OpenAI embeddings + FAISS. Reranking (optional) prioritizes the most relevant chunks per file.
4. **LLM prompts** – `ReviewEngine` sends each file’s diff + context to OpenAI, demanding strict JSON output with severity, rationale, recommendation, and patch.
5. **Aggregation & reporting** – Summaries, severity tallies, per-file diffs, and fix-ready snippets are produced. GitHub check annotations display the rationale and a well-formatted fix block.

## Running Tests
```bash
source venv/bin/activate
PYTHONPATH=. pytest
```
Tests cover chunking logic, FAISS persistence, context ingestion/retrieval, reranking fallbacks, reviewer orchestration, GitHub client behavior, FastAPI routes, and annotation formatting. CI can simply call `pytest` after installing requirements.

## Design Highlights
- **Fast retrieval**: FAISS + JSON metadata allow quick reuse across webhook calls.
- **LLM guardrails**: prompts enforce strict JSON, severity tokens, and diff-formatted patches; utilities normalize and protect against malformed outputs.
- **Extensible**: add new context chunkers, rerankers, or review heuristics by extending the `context/` package.
- **Observable**: ReviewService emits structured logs with queue depth, latency, and fallback reasons for straightforward debugging.
- **Developer-friendly**: modular code, clear interfaces, and thorough tests make it easy to extend for auto-fix workflows or metrics.

## Roadmap Ideas
- Background worker to apply fix patches automatically when users click a check-run action.
- Repo-specific tuning: severity calibration, ignored paths, heuristics.
- Persistent metrics dashboard (latency, cost, precision) for continuous evaluation.
- Built-in reviewer persona customization per language/framework.

## Contributing
PRs welcome! Please:
1. Run `pytest` locally.
2. Add/adjust tests for new logic.
3. Update documentation where relevant.

Happy reviewing!
