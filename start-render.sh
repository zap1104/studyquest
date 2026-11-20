#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput

python manage.py run_course_generation_worker --interval 5 &
worker_pid=$!
gunicorn studyquest.wsgi:application --bind "0.0.0.0:$PORT" --workers 1 --threads 3 &
web_pid=$!

cleanup() {
    kill "$web_pid" "$worker_pid" 2>/dev/null || true
    wait "$web_pid" "$worker_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

wait -n "$web_pid" "$worker_pid"
