#!/usr/bin/env bash
# Compare installed discovery and offline behavior for a locally resolved image.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1:?image required}"
export DINE_EXPECTED_SURFACE
DINE_EXPECTED_SURFACE="$(cat "$root/surface.json")"
docker run --rm -i --network none --read-only --user 1000:1000 --cap-drop ALL \
    --security-opt no-new-privileges --tmpfs /state:uid=1000,gid=1000,mode=0700 \
    --tmpfs /tmp:uid=1000,gid=1000,noexec,nosuid,size=32m \
    -e DINE_EXPECTED_SURFACE --entrypoint python "$image" - < "$root/scripts/smoke.py"
