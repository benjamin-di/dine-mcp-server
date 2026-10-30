# Design decisions

Dine MCP finds restaurants and checks public table availability across booking systems. It
returns observations and links; booking, holds, cancellation and watches are outside its scope.
See [architecture](ARCHITECTURE.md), [permissions](PERMISSIONS.md) and [roadmap](ROADMAP.md).

## Prior art and licensing

The initial search on 2026-10-29 covered restaurant, reservation and provider names in the MCP
Registry, GitHub, PyPI, npm and MCP directories. The closest open projects used a local person's
tokens, cookies or browser. None of the reviewed candidates provided the intended shared, remote,
multi-provider core, so this project implements that core rather than wrapping one.

[printing-press-library](https://github.com/mvanhorn/printing-press-library) informed TableCheck
endpoint shapes; [resdiary-mcp](https://github.com/matuspavliscak/resdiary-mcp) informed ResDiary
endpoints; [restaurant-cli](https://github.com/omarshahine/restaurant-cli) informed the provider
capability pattern. No third-party adapter code was copied. [NOTICE](../NOTICE) records credits.
The project uses Apache-2.0 for its patent grant; its Google place-source dependency,
[google-maps-keyless-mcp](https://github.com/benjamin-di/google-maps-mcp), is MIT-licensed.

## Deliberate choices

- **Async service core:** FastMCP, httpx and the place source are asynchronous. One event loop
  keeps provider fan-out and cancellation straightforward. SQLite calls are short synchronous
  operations under a lock; deployment concurrency must respect that constraint.
- **Shared public observations:** venue facts and public availability are reusable across callers
  to reduce provider load. Person ids and per-person counters remain private server state. There
  are no provider accounts or signed-in sessions today.
- **Pinned reads:** adapters name an allowlisted method, host and path operation. A POST is allowed
  only for a documented read query whose parameters belong in a body. Model-supplied booking
  links are matched, never fetched. Guest access comes first; authenticated providers need a
  separate connection design.
- **Opt-in website discovery:** a missing booking channel can be inferred from one static HTML page
  on the venue website in the current place cache. This is an explicit dynamic-host exception in
  `transport.py`; it pins GET and that source hostname for every redirect, uses the same DNS/IP
  policy, and never accepts a website as a tool argument. It has a separate credential-free HTTP
  client, global paced budget/breaker, bounded content and an optional-enrichment timeout. Parsing
  recognizes existing provider URLs only, executes no script, fetches no assets and refuses
  multiple distinct venue refs. Provisional channels expire within one day and the source TTL,
  never become permanent aliases, and disappear when discovery is disabled. Only recognized
  links are retained, with queries removed except Zenchef's reviewed numeric `rid`; HTML and cookies are discarded. Failures use a
  15-minute negative cache and leave directory results usable.
- **Four tools:** find, get, check and status expose explicit input/output schemas and read-only
  annotations. A reviewed snapshot and byte budget guard the public surface. Status needs valid
  HTTP credentials but no gateway person header because it reports aggregate health.
- **Multiple entry points:** MCP, CLI and Python use the same service; standalone use does not
  depend on a particular gateway or secret manager.

## Hardening guarantees

One context carries the end-to-end deadline and actual HTTP dispatch count through nested service
work. Completed adapter batches publish observations before the next request; incomplete windows
can retain positive slots without claiming complete coverage. TableCheck cache keys include the
requested time window, including stale fallback. Absent dates remain unknown.

The provider boundary streams bounded bodies, charges and paces each redirect, checks the durable
breaker after queueing and pacing, and honours Retry-After up to one day. A fair per-person queue
is bounded to 128 waiting requests per provider. DNS resolves once per connection; all answers must
be public, and the socket connects to the vetted IP while TLS keeps the original hostname.
The httpx pool hook for this policy is private API, isolated and guarded by a construction test.
One serving process per state directory is supported: SQLite shares durable budgets/breakers,
but pacing and request scheduling are process-local.

HTTP requires an explicit Host allowlist and same-origin or explicitly allowed Origin. Duplicate
authority headers and bodies over 1 MB are refused before JSON parsing. The global disable switch
guards all venue service entry points. Trusted gateway identity creates a person record on first
contact; membership admission/revocation remain the gateway's responsibility.

Google-derived channels expire with their seven-day source cache. Startup, periodic cleanup and
cache writes delete expired data; reads filter expired channels immediately. Public provider and
curated facts retain their own provenance. Explicit closed days in dated hours can avoid provider
requests, without extrapolating beyond the source week or uncertain holidays.

Status stays offline unless `check=true`; explicit checks share budgets and a durable 15-minute
cooldown. Completion logs join tool, classified outcome, duration and hashed person; validated
W3C traceparent metadata propagates through provider reads. Progress uses the client's token;
stdio supports notifications, while stateless JSON HTTP only carries the final result.

No deployment or public release is implied by these source guarantees. Qualification scope and
remaining external acceptance are documented in [verification](VERIFICATION.md).
