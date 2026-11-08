#!/usr/bin/env bash
# Compare discovery from the installed entry point with the reviewed source contract.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1:?image digest required}"
revision="${2:?source revision required}"
version="${3:?package version required}"
[[ "$image" =~ @sha256:[a-f0-9]{64}$ ]] || { echo 'An exact image digest is required' >&2; exit 2; }
docker pull --quiet "$image" >/dev/null
[ "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" = "$revision" ]
[ "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")" = "$version" ]
docker run --rm --network none --read-only --user 1000:1000 --tmpfs /state:uid=1000 \
    --entrypoint python "$image" \
    -c 'import sys, zoneinfo; from dine_mcp.identity import BUILD; from dine_mcp.server import create_server; zoneinfo.ZoneInfo("Asia/Singapore"); assert (BUILD.service, BUILD.version, BUILD.source_revision) == ("dine", sys.argv[1], sys.argv[2]); assert create_server(stdio=True).name == "dine"' \
    "$version" "$revision"
rc=0
timeout 120 docker run --rm --network none --read-only --tmpfs /state:uid=1000 "$image" || rc=$?
[ "$rc" = 1 ] || { echo "Expected missing-bearer refusal, got exit $rc" >&2; exit 1; }
reply="$(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
    | timeout 60 docker run --rm -i --network none --read-only --tmpfs /state:uid=1000 "$image" --transport stdio)"
printf '%s' "$reply" | python3 -c 'import json,sys; r=json.load(sys.stdin); info=r.get("result", {}).get("serverInfo", {}); assert r.get("id") == 1 and info.get("name") == "dine" and info.get("version") == sys.argv[1]' "$version"

bash "$root/scripts/smoke-contract.sh" "$image"
