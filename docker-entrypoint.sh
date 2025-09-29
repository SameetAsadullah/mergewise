#!/bin/bash
set -euo pipefail

ROLE=${1:-web}
shift || true

case "$ROLE" in
  web)
    exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
    ;;
  worker)
    exec celery -A src.task_queue:celery_app worker --loglevel="${CELERY_LOG_LEVEL:-info}" "$@"
    ;;
  beat)
    exec celery -A src.task_queue:celery_app beat --loglevel="${CELERY_LOG_LEVEL:-info}" "$@"
    ;;
  *)
    exec "$ROLE" "$@"
    ;;
esac
