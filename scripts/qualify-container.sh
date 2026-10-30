#!/usr/bin/env bash
# Build locally and exercise temporary containers, without publishing or changing services.
# Run from a clean source checkout/archive on a Docker-capable build host.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
: "${SOURCE_REVISION:?Set SOURCE_REVISION to the exact source commit being checked}"
[[ "$SOURCE_REVISION" =~ ^[a-f0-9]{40}$ ]] || { echo 'SOURCE_REVISION must be a full commit' >&2; exit 2; }
work="$(mktemp -d)"
run_id="$(basename "$work" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
deps="dine-codecheck-deps:$run_id"
image="dine-codecheck:$run_id"
container="dine-codecheck-$run_id"
cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker image rm "$image" "$deps" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT
export MCP_BEARER_TOKEN=synthetic-container-qualification-bearer-token
# Configuration rendering never starts the Compose service or pulls its image.
docker compose -f compose.yml config --quiet
printf 'Building temporary dependency image\n'
docker build --progress=plain -f Dockerfile.deps -t "$deps" . >"$work/deps.log" 2>&1 || {
    tail -60 "$work/deps.log"; exit 1;
}
printf 'Building temporary application image\n'
docker build --progress=plain -f Dockerfile -t "$image" \
    --build-arg DINE_MCP_DEPS_IMAGE="$deps" --build-arg VCS_REF="$SOURCE_REVISION" \
    --build-arg VERSION=code-qualification . >"$work/build.log" 2>&1 || {
    tail -60 "$work/build.log"; exit 1;
}
[[ "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")" == "$SOURCE_REVISION" ]]
[[ "$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")" == code-qualification ]]
options=(--rm --network none --read-only --user 1000:1000 --cap-drop ALL \
    --security-opt no-new-privileges --tmpfs /state:uid=1000,gid=1000,mode=0700 \
    --tmpfs /tmp:uid=1000,gid=1000,noexec,nosuid,size=32m -e DINE_PLACES=none)
# HTTP must fail closed without a bearer.
rc=0
docker run "${options[@]}" "$image" >"$work/refusal.log" 2>&1 || rc=$?
[[ "$rc" == 1 ]] || { cat "$work/refusal.log"; echo 'HTTP did not refuse absent authentication' >&2; exit 1; }
# A real client launches the packaged entry point over stdio, without source mounts.
docker run -i "${options[@]}" --entrypoint python "$image" - <<'PY'
import asyncio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
async def main():
    async with Client(StdioTransport(
        command="dine-mcp", args=["--transport", "stdio"],
        env={"DINE_STATE_DIR": "/state", "DINE_PLACES": "none", "DINE_WEBSITE_DISCOVERY": "1"},
    )) as client:
        assert len(await client.list_tools()) == 4
        result = await client.call_tool("dine_status", {})
        assert len(result.structured_content["providers"]) == 3
        assert result.structured_content["website_discovery"]["enabled"] is True
        assert result.structured_content["website_discovery"]["used_today"] == 0
asyncio.run(main())
PY
# Temporary HTTP fixture: no published ports, no external network, no shared state volume.
docker run -d "${options[@]}" --name "$container" -e MCP_BEARER_TOKEN "$image" > /dev/null
docker exec -i "$container" python - <<'PY'
import os
import time
import httpx
url = "http://127.0.0.1:8000"
with httpx.Client(timeout=2, trust_env=False) as client:
    for attempt in range(80):
        try:
            if client.get(url + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    else:
        raise SystemExit("packaged HTTP server did not become healthy")
    body = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"dine_status","arguments":{}}}
    headers = {"accept":"application/json, text/event-stream", "mcp-protocol-version":"2025-11-25"}
    assert client.post(url + "/mcp", json=body, headers=headers).status_code == 401
    headers["authorization"] = "Bearer " + os.environ["MCP_BEARER_TOKEN"]
    assert client.post(url + "/mcp", json=body, headers={**headers,"host":"untrusted.example"}).status_code == 403
    result = client.post(url + "/mcp", json=body, headers=headers)
    assert result.status_code == 200 and len(result.json()["result"]["structuredContent"]["providers"]) == 3
print("Packaged HTTP authentication, authority validation and status passed")
PY
printf 'Container qualification passed for %s\n' "$SOURCE_REVISION"
