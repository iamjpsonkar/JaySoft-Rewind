#!/bin/sh
set -eu

case "${1:-}" in
    "") docker build -t rewind-local:alpha . ;;
    --skip-build) ;;
    *) echo "Usage: $0 [--skip-build] (run from repository root)" >&2; exit 2 ;;
esac

docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    rewind-local:alpha python -m scripts.offline_smoke
