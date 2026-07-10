#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="packages/domain/src:packages/application/src:adapters/operations/src:packages/application/tests"
python -m unittest discover -s packages/domain/tests -v
python -m unittest discover -s packages/application/tests -v
