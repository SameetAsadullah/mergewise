# MergeWise Architecture

MergeWise is designed as a modular, service-oriented FastAPI application that layers retrieval, LLM reasoning, and GitHub orchestration. This document walks through the major components and how they interact.

## Components

### FastAPI Transport (`app.py`)
- Exposes REST endpoints: `/health`, `/review`, `/review/github`, and `/github/webhook`.
- Delegates all review execution to `ReviewService`, keeping HTTP handlers thin.
- Serializes structured review results (or queue job IDs) back to the caller.

### Application Services (`src/services/review.py`)
- `ReviewService` orchestrates queue vs. inline execution, context construction, and GitHub check updates.
- `ReviewQueue` wraps Celery enqueue helpers so the app can detect queue failures and fall back gracefully.
- `ReviewOutcome` carries either a Celery `task_id` or inline results plus any queue error for observability.
- Logging is centralized through `src/logging_config.configure_logging`, which installs rotating file handlers and optional console output for all modules.

### Review Engine (`src/reviewer.py`)
- `ReviewEngine` orchestrates diff parsing, context retrieval, and LLM prompts.
- `DiffParser` splits unified diffs into file-specific chunks.
- Uses `OpenAI` chat completions to obtain structured findings per file.
- Aggregates findings into summary stats and per-file results.

### Context Retrieval (`src/context/`)
- `ContextConfig`: centralizes chunk sizes, retrieval limits, reranker configuration.
- `ContextChunker`: AST-aware chunking for Python and heuristics for other languages.
- `FaissVectorStore`: JSON metadata + FAISS inner-product index for embeddings.
- `RepositoryContextService`: fetches repo tree/contents (via GitHub), embeds files, persists index, and retrieves top-k context for a diff.
- Optional `OpenAIReranker` reorders candidates for higher relevance.

### GitHub Integration (`src/github.py`)
- `GitHubClient` fetches PR metadata/diffs, creates check runs, and appends annotations.
- Uses GitHub App installation tokens (generated in `src/security.py`).

### Utilities (`src/utils.py`)
- `result_to_check_conclusion`: maps review results to GitHub check status.
- `build_check_summary_markdown`: builds markdown summary for the check run.
- `build_github_annotations`: converts findings into GitHub annotation payloads.
- Diff helpers locate anchors and map to line numbers.

## Data Flow
1. **Webhook or Manual Review** – PR event triggers `/github/webhook` (or `/review/github`).
2. **Orchestration** – `ReviewService` enqueues work (Celery/Redis) or runs the review inline based on `ENABLE_TASK_QUEUE`.
3. **Fetch PR Context** – `GitHubClient` retrieves title, diff, and base SHA.
4. **Ensure Index** – `RepositoryContextService` rebuilds or reuses FAISS index for base commit and touched files.
5. **Review Loop** – `ReviewEngine`:
   - Splits diff per file.
   - Retrieves contextual snippets (docs, code) from FAISS.
   - Prompts OpenAI with diff + context to get structured findings.
5. **Aggregation** – Summaries, counts, and per-file diffs collected.
6. **GitHub Reporting** – `ReviewService` calls `build_github_annotations` and `create_or_update_check_run` to publish summary + annotations.

## Extensibility Points
- Swap embedding model or FAISS index type by configuring `ContextConfig`.
- Add new chunkers for other languages by extending `ContextChunker`.
- Plug in alternate rerankers (BM25, cross-encoder) via the `ContextReranker` protocol.
- Customize review prompts or severity logic in `ReviewEngine`.
- Introduce auto-fix workflows by consuming the existing structured `patch` field.

## Deployment Considerations
- Needs OpenAI API credentials and GitHub App credentials.
- FAISS index stored on disk (configurable path); ensure writable persistent storage in production.
- Set `ENABLE_CONTEXT_INDEXING=false` for testing/lightweight deployments.
- Rate-limit or batch LLM calls to manage cost/latency.

## Sequence Diagram
```
GitHub -> FastAPI (/github/webhook)
FastAPI -> ReviewService: process_pull_request_event
ReviewService -> ReviewQueue: enqueue_github (if enabled)
alt inline fallback
    ReviewService -> GitHubClient: get PR details
    ReviewService -> RepositoryContextService: ensure_index(paths)
end
RepositoryContextService -> GitHub: fetch tree + file contents
RepositoryContextService -> OpenAI: create embeddings
RepositoryContextService -> FaissVectorStore: replace/add documents
FastAPI -> ReviewEngine: review(pr_title, diff, context_service)
ReviewEngine -> RepositoryContextService: retrieve_context(file)
ReviewEngine -> OpenAI: chat.completions.create(...)
ReviewEngine -> FastAPI: summary + findings
FastAPI -> utils: build summary + annotations
FastAPI -> GitHubClient: create_or_update_check_run(...)
```

## Testing Strategy
- Unit tests for chunking, vector store, context service, reranker, review engine, GitHub client, and FastAPI endpoints (`tests/`).
- Deterministic OpenAI/GitHub stubs in `tests/conftest.py` ensure offline reproducibility.
- Additional `test_utils.py` ensures annotation formatting remains stable.

## Roadmap Hooks
- Background job queue for applying fix patches.
- Observability (latency, cost, coverage) dashboard fed from review metrics.
- Repo-specific policy adjustments (ignored paths, severity thresholds).

This architecture balances modularity, retrieval quality, and GitHub-native integration, making it straightforward to extend with auto-fix actions, analytics, or alternative models.
