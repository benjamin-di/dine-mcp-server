# Architecture

```text
 MCP (FastMCP)       CLI       Python
          \           |         /
      DineService (one deadline / dispatch budget)
        /             |                  \
    Resolver     Planner + Checker      definitions / overrides
       |           public cache          TOML, validated
 places_google        |
       |         provider adapters
 google-maps-         |
 keyless-mcp     transport.py (pinned reads, paced fair queue, DNS policy)
       \              /
       SQLite: venue aliases, place cache, roster, budgets, breakers, canary cooldowns
```

## Resolution and storage

Find takes a name, Google place id or booking link. Links are cleaned and matched, never fetched;
a pinned provider operation verifies the matched venue. Names resolve only when a place matches
clearly (similarity at least 0.85, 0.1 ahead of the next candidate). Otherwise candidates and their
place ids are returned. Curated facts can add contact channels or release rules.

With `DINE_WEBSITE_DISCOVERY=1`, an otherwise contact-only place can acquire a provisional booking
channel from its source-owned website. The source must match the current cached place details;
model-supplied URLs never enter the website reader. One static HTML page is parsed for supported
provider links in anchor, iframe and button attributes. Multiple distinct venue refs remain
ambiguous. Detected channels have `source=website`, `verified=false`, no durable provider alias,
and at most one day of validity bounded by the source's expiry. Only query-free recognized links
are cached; no raw HTML is retained. A changed website invalidates its previous association.
Disabling discovery removes its cache and copied channels. Concurrent callers for one place share
a single read; failures are cached for 15 minutes and preserve directory/contact results.
The five-second enrichment budget includes waiting for that shared read. Source ownership is
rechecked before publication and reuse; refreshing the source during a read never extends that
read's original source expiry.

Venue ids are minted once. Place ids, provider references and merged ids are aliases. Google
place ids persist; other Google facts live in the seven-day place cache. Copied channels carry
source provenance and the same expiry. Reads immediately filter expired channels; startup, cache
writes and the serving cleanup task remove expired stored data. Provider and curated facts do not
inherit Google's expiry. State is private; public observations are deliberately shared.
An explicit provider-link lookup refreshes its independently verified channel and provider facts,
including for an existing Google venue. Place refreshes preserve that provenance. Sparse search
rows cannot replace richer place details or renew their TTL; newly observed booking links can
update the cached details while retaining their original expiry.

## Planning, coverage and deadlines

Each find/get/check carries one end-to-end deadline (default 30 seconds) through nested work.
Availability plans each venue × channel before dispatch. Public channels are checked, signed-in
channels return `not_connected`, listing channels return their link, and contact-only venues
return contact details. More than 20 estimated provider requests refuses the call. Actual HTTP
dispatches, including redirects and metadata reads, also share a 20-request cap.
Planning uses cached party minima/maxima, release windows and venue time zones before estimating cost.

Providers run concurrently, with one request at a time per provider. The checker cancels and joins
unfinished work at the deadline. Completed adapter batches enter the cache before the next read,
so later failure or timeout retains their dates and slots. Unknown coverage is never negative
availability. Partial time windows can retain positive slots without marking the day fully checked.
Unexpected adapter errors remain per-venue failures; policy errors still fail the call explicitly.
Missing-coverage warnings and provider caveats are retained together. TableCheck's unreleased-date
caveat survives incremental publication and cache reuse; equal start/end times mean one local time.

Cache keys contain provider, venue reference, party size and local date. Windowed providers
(TableCheck) also include the exact requested time window. Fresh reuse lasts two minutes; during
pushback, cached observations up to ten minutes old are returned with their original timestamp
and cache origin. Time filtering assigns after-midnight slots to the preceding service date and
preserves provider offsets across DST and the date line.

Party limits and release windows come from provider metadata. Past and unreleased dates are not
requested. Explicit closed days from Google's dated hours can avoid provider reads entirely.
This shortcut requires an explicit Closed description within the fetched seven-day horizon, no
opening interval overlapping the day and no uncertain holiday. Missing hours never imply closed.
Malformed hours are also unknown. Provider metadata expires after one hour and is bounded to
5,000 venue entries. Availability requires a known venue time zone: a fresh place-source zone can
fill a provider metadata gap for the current check without changing the provider metadata cache.
Naive TableCheck timestamps are interpreted in that venue zone, independently of the server zone.

## Network and HTTP boundaries

`transport.py` is the sole provider network module. Every read names a pinned method, HTTPS host
and path. POST needs a recorded read-only reason. Each redirect is revalidated, paced and charged;
POST redirects are refused. Bodies are streamed with an 8 MB decoded-content cap. Provider hosts
resolve once per connection: every address must be public, and the socket uses the vetted IP while
TLS retains the provider hostname. Environment proxies and HTTP retries are disabled.

The optional website reader is the deliberate dynamic-host exception in the same module. It pins
GET to the validated source hostname, refuses IP literals and credentials, and follows HTTPS
redirects only on that exact host. Known provider hosts cannot enter this reader, so their pinned
operations and budgets cannot be bypassed. A source website already containing a recognized
booking URL needs no fetch. The reader shares the DNS connection policy, per-call dispatch cap,
fair queue, pacing and persistent budgets/breakers. Its separate client sends no cookies,
authorization, referer or trace context. Tracking parameters/fragments are stripped from the
source URL; other source queries and all redirect queries are refused before dispatch.
Decoded content is limited to 512 kB; only HTML is parsed. All websites together have a daily
budget of 50 requests (half per person), a one-second minimum interval and a five-second overall
optional-enrichment timeout. Provider canaries and health never trigger website discovery.

One serving process per state directory is supported. Requests queue FIFO within a person and
round-robin between people, with at most 128 waiting requests per provider. Shared UTC budgets and
per-person shares persist in SQLite. The durable breaker is checked after queueing and pacing.
429, 401/403, challenge pages or cf-mitigated open it for 5 minutes, then 30 minutes, 2 hours and
8 hours on repeated pushback. Retry-After may extend this to at most one day. No challenge is solved
and no request is automatically retried.

HTTP checks authentication, an explicit Host allowlist and same-origin or an explicitly allowed
Origin. Ambiguous authority headers and request bodies over 1 MB are refused before JSON parsing.
`/health` is liveness only. Global disable guards find, get and check before external work; status
stays available but skips active checks while disabled.

## Identity, logs and monitoring

HTTP resolves identity from a verified standalone bearer or from the trusted gateway's person
header. Gateway first contact creates a local record; the gateway owns admission and revocation.
No tool argument selects another person. See [permissions](PERMISSIONS.md).
Standalone users must have distinct token hashes. User additions use a file lock and a private
temporary file, atomically replacing the users file without losing concurrent additions.

Each completion log joins the tool, outcome code, duration and keyed person hash under a request
id. Arguments are fingerprinted, never recorded raw. Valid W3C traceparent request metadata flows
to provider reads. Client-requested progress notifications are available over stdio; stateless
JSON HTTP carries the final result only.

Status is offline by default. Explicit canaries use ordinary pacing, budgets, breaker and call
deadline, with a durable 15-minute cooldown claimed before dispatch. The operator drift-report
entry point emits JSON without registering or running a schedule automatically.

Whole-call errors use a closed envelope; provider failures appear as per-channel statuses.
Provider and venue text is always data. Safety tests guard the read-only network boundary, while
[verification](VERIFICATION.md) describes the synthetic transport and protocol checks.

## Optional App and provider discovery

The `DINE_APPS` composition switch defaults off. When enabled, the check tool advertises a static
`text/html;profile=mcp-app` resource; its SDK bundle and dependency notices ship in the wheel.
No external CSP domains are admitted. Tool results reach the page through the host; local filters
and explicit host-mediated links require no server-side personal state or additional fetches.
The text mirror and tool arguments remain the same. `make ui-check` rebuilds in memory and
compares the exact shipped page/notices before exercising the SDK handshake in a synthetic DOM.

Provider-search fallback runs only when the place source fails or has no venues or candidates.
An ambiguous place-source answer is preserved. Each adapter searches a bounded catalogue and
verifies venue details on pinned read paths; the service preserves multiple matches, filters an
explicit country, caches verified provider facts and labels coverage as non-exhaustive.
Tabelog and Korean CatchTable are experimental listing providers; Zenchef/Formitable are separate
experimental guest-read contracts. Missing metadata time zones require fresh place-source evidence.
