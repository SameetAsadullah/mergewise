# MergeWise

MergeWise is an AI powered pull request reviewer that produces context aware, actionable feedback directly inside GitHub Checks. It blends retrieval augmented generation (FAISS + OpenAI) with a production ready FastAPI backend, optional Celery workers, and structured telemetry.

## Quick Start (Hosted GitHub App)
Want reviews without deploying anything? Share or install the hosted MergeWise GitHub App:

1. Open the installation page: <https://github.com/apps/mergewise>.
2. Choose the repositories you want reviewed. The app only requires **Checks (Read & Write)** and **Pull Requests (Read)** permissions.
3. MergeWise automatically reviews pull requests and publishes a Check Run with per line annotations, severities, rationale, and fix-ready suggestions.

All secrets stay in the infrastructure you manage; installing organizations never see or provide credentials.

## Self-Hosted Setup (Developers & Recruiters)
Run MergeWise locally or in your own cloud to inspect the architecture, extend features, or demo operational maturity.

### 1. Clone & install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment
Create a `.env` file (loaded via `python-dotenv`) with the required settings:
```
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
GITHUB_APP_ID=<your-github-app-id>
GITHUB_APP_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
GITHUB_WEBHOOK_SECRET=<optional>
ENABLE_TASK_QUEUE=0                # set to 1 when running Celery/Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
LOG_FILE=logs/mergewise.log
LOG_LEVEL=INFO
LOG_FORCE=1
LOG_CONSOLE=1
```
Additional knobs (chunk sizes, reranker toggle, queue depth, logging fallbacks) live in `src/settings.py` and `src/context/config.py`.

### 3. Run the API
```bash
source venv/bin/activate
uvicorn app:app --reload
```
Useful endpoints:
- `GET /health` – basic readiness probe
- `POST /review` – review an arbitrary diff payload
- `POST /review/github` – review by owner/repo/pr number
- `POST /github/webhook` – GitHub webhook handler (HMAC verified if secret set)

### Optional: Background queue (Celery + Redis)
For large PRs or multi-tenant installs, offload work to Celery workers:

1. Start Redis locally (or point `CELERY_BROKER_URL` to a managed instance):
   ```bash
   docker run --rm -p 6379:6379 redis:7-alpine
   ```
2. Toggle queue mode by setting `ENABLE_TASK_QUEUE=1` in `.env`.
3. Launch a worker alongside the API:
   ```bash
   source venv/bin/activate
   celery -A src.task_queue:celery_app worker --loglevel=info
   ```

Workers share the rotating file handler and structured logs, emitting queue depth, task durations, and fallback reasons. If Redis is unreachable, reviews fall back to inline execution and responses include a `queue_error` flag for visibility.

### Optional: Docker workflow
```bash
docker build -t mergewise .
docker run --rm -p 8000:8000 --env-file .env mergewise web
# Optional worker in a second container
docker run --rm --env-file .env mergewise worker
```

*(If you prefer inline execution inside the container, override `ENABLE_TASK_QUEUE=0` when running `mergewise web`.)*

## Self-Managed GitHub App
Prefer to run everything yourself?

1. Create a GitHub App with **Checks (Read & Write)** and **Pull Requests (Read)** permissions.
2. Set the webhook URL to `/github/webhook` and reuse the same secret in `.env`.
3. Deploy MergeWise (Render, Fly.io, Railway, AWS, etc.) using the provided Dockerfile. The entrypoint script exposes roles:
   - `./docker-entrypoint.sh web` → FastAPI service
   - `./docker-entrypoint.sh worker` → Celery worker
4. Install the app on target repositories. The service automatically discovers the installation ID and mints scoped tokens before each review.

## Why This Stands Out in Production
- **Production readiness** – Celery/Redis queue, rotating file logs, structured telemetry, and validation show real deployment discipline.
- **Context aware intelligence** – FAISS embeddings, AST-aware chunking, and reranking demonstrate modern RAG patterns beyond prompt tinkering.
- **Security conscious** – HMAC webhook verification, short-lived GitHub installation tokens, and inline fallback for restricted environments.
- **Extensible architecture** – `ReviewService`, `ReviewQueue`, context providers, and validators keep new features isolated and testable.

## Project Structure
```
├─ app.py                 # FastAPI entrypoint & routes
├─ src/
│  ├─ context/            # Context indexing + retrieval (config, chunking, FAISS store, reranker, service)
│  ├─ github.py           # GitHub REST client + helpers
│  ├─ reviewer.py         # ReviewEngine (diff parsing, LLM prompts, aggregation)
│  ├─ services/           # Application-level orchestration (ReviewService, ReviewQueue)
│  ├─ security.py         # GitHub App JWT + installation token helpers
│  ├─ settings.py         # Centralized configuration (env-driven)
│  └─ ...                 # task_queue, utils, schemas, etc.
├─ docs/                  # Architecture, deployment, and queue guides
├─ requirements.txt       # Runtime dependencies
├─ Dockerfile             # Containerized deployment
├─ tests/                 # pytest suite with fixtures + integration tests
└─ README.md              # Project documentation
```

## How Reviews Work
1. **Request orchestration** – `ReviewService` decides between inline execution and queueing, prepares context services, and captures telemetry.
2. **Diff parsing** – `DiffParser` splits the unified diff into file-specific chunks.
3. **Context retrieval** – `RepositoryContextService` indexes repository blobs with OpenAI embeddings + FAISS; optional reranking heightens relevance.
4. **LLM prompts** – `ReviewEngine` sends the diff + context to OpenAI, enforcing strict JSON findings (severity, rationale, recommendation, patch).
5. **Aggregation & reporting** – Summaries, severity counts, and per-file diffs feed into GitHub Check annotations with fix-ready code snippets.

## Running Tests
```bash
source venv/bin/activate
PYTHONPATH=. pytest
```
The suite covers chunking, FAISS persistence, context retrieval, reranking resiliency, reviewer orchestration, GitHub client behaviour, FastAPI routes, and annotation formatting.

## Design Highlights
- **Fast retrieval** – FAISS + JSON metadata allow quick reuse across webhook calls.
- **LLM guardrails** – prompts enforce strict JSON, severity tokens, and diff-formatted patches; utilities normalize model output.
- **Extensible** – plug in new chunkers, rerankers, or heuristics by extending `context/` or `services/` components.
- **Observable** – unified logging writes queue depth, durations, fallback reasons, and worker outcomes to rotating files.
- **Developer-friendly** – modular code, clear interfaces, and thorough tests lower the barrier for new contributors.

## Roadmap Ideas
- Auto-apply fix patches via GitHub Check actions.
- Repo-specific policy tuning (ignored paths, severity thresholds).
- Metrics dashboards for latency, model spend, and precision/recall tracking.
- Reviewer personas (language/framework hints) to tailor prompts.

## Contributing
PRs welcome! Please:
1. Run `pytest` locally.
2. Add/adjust tests for new logic.
3. Update documentation where relevant.

Happy reviewing!
