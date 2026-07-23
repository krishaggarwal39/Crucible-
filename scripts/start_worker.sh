#!/bin/bash
set -a
source /Users/krishaggarwal/Downloads/crucible/.env
set +a
export PYTHONPATH=/Users/krishaggarwal/Downloads/crucible/src
cd /Users/krishaggarwal/Downloads/crucible
celery -A backend.worker.celery_app worker --loglevel=info -c 2
