#!/bin/bash
set -a
source /Users/krishaggarwal/Downloads/crucible/.env
set +a
export PYTHONPATH=/Users/krishaggarwal/Downloads/crucible/src
cd /Users/krishaggarwal/Downloads/crucible
uvicorn backend.main:app --host 0.0.0.0 --port 8000
