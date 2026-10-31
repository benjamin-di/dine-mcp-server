# dine-mcp

<!-- mcp-name: io.github.benjamin-di/dine-mcp -->

Restaurant **table availability** across the booking systems restaurants actually use, in any
country, for AI assistants ([MCP](https://modelcontextprotocol.io)), the command line and Python.

Ask "can we get a table for four at Harbour Grill on Friday evening?" and the assistant finds the venue,
works out which booking system it uses (from Google Maps' "Reserve a table" link, the venue's
booking page, or curated facts), checks the open times in the venue's own time zone, and gives you
the provider's booking page, prefilled where it can be. **dine-mcp never books, holds or cancels
anything:** you book yourself, on the provider's page.

<!-- providers:start -->
| Provider | Countries | Mode | Maturity | Last verified live |
|---|---|---|---|---|
| CatchTable (Korea) | KR | listing | experimental | unverified |
| Formitable (Zenchef) | NL, BE, FR, DE, GB | public | experimental | unverified |
| ResDiary | GB, IE, AU, NZ, AE, SG, HK, MY, ZA | public | beta | 2026-10-29 |
| SevenRooms | HK, GB, US, AE, AU, SG, IE, CA | public | beta | 2026-10-29 |
| Tabelog | JP | listing | experimental | unverified |
| TableCheck | JP, HK, SG, KR, TW, TH, AU, GB, US | public | beta | 2026-10-29 |
| Zenchef | FR, NL, BE, GB, DE, CH, ES, IT | public | experimental | unverified |
<!-- providers:end -->

SevenRooms returns bookable/request slots, experiences and card flags; ResDiary includes seating
areas. TableCheck does not publish its release window, so unreleased dates can look full.
Other booking pages are link-only; phone, messaging and website-only venues return contact details.
Optional website discovery can detect a supported widget when the place listing has no booking link.

More providers come as definitions and adapters ([CONTRIBUTING.md](CONTRIBUTING.md)); what is
next is in [docs/ROADMAP.md](docs/ROADMAP.md).

> Unofficial. Not affiliated with any booking provider or with Google. The providers' terms
> restrict automated access: dine-mcp reads only their public booking widgets, for a person
> checking a table for themselves, at human pace, and never books. Use it for personal, low-volume
> purposes, at your own risk. Venue lookups use Google Maps through
> [google-maps-keyless-mcp](https://pypi.org/project/google-maps-keyless-mcp/), whose terms note
> applies too.

## Quick start

**Release 0.2.6.** From a source checkout, run:

```sh
uv sync --locked
uv run dine-mcp --transport stdio
```

The equivalent package command and client configurations are:

```sh
uvx dine-mcp --transport stdio            # for a local MCP client (Claude Desktop, Claude Code, …)
```

Claude Code: `claude mcp add dine -- uvx dine-mcp --transport stdio`. Claude Desktop
(`claude_desktop_config.json`):

```json
{"mcpServers": {"dine": {"command": "uvx", "args": ["dine-mcp", "--transport", "stdio"]}}}
```

The command line does the same without an assistant:

```sh
uvx dine-mcp find "Harbour Grill" --area "Raffles Place" --country SG
uvx dine-mcp check v_abc123def456 --date 2026-11-06 --from-time 18:00 --to-time 21:30 --party 4
```

## Tools

| Tool | What it does |
|---|---|
| `dine_find_venue(name, area, country \| url \| google_place_id, limit)` | Identifies one restaurant and its booking channels. Ambiguous names return candidates with their Google place ids, never a guess. A booking link is only matched against known providers, never fetched. |
| `dine_get_venue(venue_id)` | The venue in full: each provider's booking rules (days ahead, party size limits), booking links, opening hours. |
| `dine_check_availability(venue_ids ≤5, date_from, date_to ≤7 days, time_from, time_to, party_size, kinds, response_format)` | One result per venue and channel: slots in the venue's time zone, coverage, `observed_at`, the booking link. |
| `dine_status(check=false)` | Providers, budgets and pauses. Optional paced canaries share a durable 15-minute cooldown. |

All tools are read-only. Every result also comes as short text for clients that show only text.

To check a restaurant, resolve it with `dine_find_venue`, then call `dine_check_availability`.
Ask for missing dates and diner count first, and state the time window used (the API defaults
to 17:00–22:30). `dine_status` diagnoses the service; it does not check a restaurant's tables.
Failed requests and unchecked dates mean unknown availability, never evidence that it is sold out.

**Statuses** (per venue and channel): `ok`, `none_available`, `not_yet_open` (the dates are not
released yet; `opens_at` when known), `party_too_small` (with `min_party`) or
`party_too_large` (with the venue's online maximum and its
contact), `closed`, `contact_only`, `unsupported` (a booking system dine-mcp does not read; the
link is given), `blocked` / `rate_limited` (the provider asked us to slow down), `unavailable`,
`unknown_venue`, `skipped_deadline`. One provider failing never fails the call.

**Limits:** at most 5 venues and 7 days per availability call. Plans estimated above 20 provider
requests are refused with `too_broad`; actual dispatches, including redirects, share the same cap.
Find, get and check share a 30-second end-to-end deadline across lookup, planning and provider work.
Completed batches survive later errors and deadlines; omitted dates remain unknown, never evidence
of no availability. Observations are cached for two minutes. TableCheck reuse requires the same
time window; older observations returned during pushback are labelled with their original time.
Progress notifications are supported over stdio; stateless JSON HTTP returns the completed result.

**Errors** are tool results with `isError` whose text is
`{"error": {"code", "retryable", "retry_after_seconds", "action", "next_step"}}`.

## Running a server

A hardened [Compose example](compose.yml) provides a read-only root, dropped capabilities and a
private state volume. Set `DINE_IMAGE` to a locally built image until the first image is published.
Configure the HTTP Host/Origin allowlists for your proxy before accepting remote clients.

Before an image is published, use `uv run dine-mcp --transport http` from the source checkout
with the environment below. After the first image release:

```sh
docker run -d -p 127.0.0.1:8000:8000 -v dine-state:/state \
  -e MCP_BEARER_TOKEN="$(openssl rand -hex 32)" ghcr.io/benjamin-di/dine-mcp-server:latest
```

The MCP endpoint is `/mcp` (streamable HTTP, stateless JSON); `/health` is open. Who is calling
decides which share of the request budget is used:

| Mode | Set | Person |
|---|---|---|
| single | `MCP_BEARER_TOKEN` | one person, `owner` |
| users (standalone multi-user) | `DINE_USERS_FILE`; `dine-mcp users add <id> --file users.toml` prints that user's token once | the user whose token was presented |
| gateway | `MCP_BEARER_TOKEN` (the gateway's) and `DINE_PERSON_HEADER` (e.g. `X-Person`) | the validated person the authenticating gateway asserts; a record is created on first contact |
| stdio | `--transport stdio` | the local user |

| Variable | Default | Meaning |
|---|---|---|
| `DINE_STATE_DIR` | `$XDG_STATE_HOME/dine-mcp` (`/state` in the image) | venues, budgets, breakers, the roster |
| `DINE_OVERRIDES` | none | operator switches (below) |
| `DINE_PLACES` | `google` | `none` turns off venue search by name (links still work) |
| `DINE_GOOGLE_DAILY_BUDGET` | `200` | Google Maps requests per day |
| `DINE_WEBSITE_DISCOVERY` | `0` | `1` enables bounded HTML discovery on the venue website supplied by the place source |
| `DINE_DEADLINE_SECONDS` | `30` | end-to-end timeout for find/get/check and explicit canaries |
| `DINE_HTTP_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | explicit HTTP Host allowlist; add your reverse proxy hostname |
| `DINE_HTTP_ALLOWED_ORIGINS` | empty | comma-separated exact cross-origin HTTP origins; same-origin is accepted |
| `DINE_APPS` | `0` | Set to `1` for the optional MCP Apps availability grid; text results remain available |
| `DINE_HOST`, `DINE_PORT` | `127.0.0.1`, `8000` | HTTP listen address |

**Overrides** (`DINE_OVERRIDES`, TOML) are the operator's only switch, applied at start-up:

```toml
disabled = false                 # true: find/get/check refuse before external work
[providers.tablecheck]
access_mode = "listing"          # stop reading availability; return links only (never raise a mode)
[providers.sevenrooms]
enabled = false                  # off entirely
```

Each provider is paced to one request every two seconds, one at a time, within a daily budget
shared by everyone on the server (each person may use half of it), and pauses for at least five
minutes when it pushes back (429, 401 or 403, or a bot check). It never retries or solves a challenge.

When website discovery is enabled, find/get/check may read the current place-source website if
no booking channel is known. It recognizes supported anchors and iframe/button attributes without
JavaScript or crawling. Multiple distinct venue widgets are ambiguous and produce no inferred
channel. Detected links are provisional (`source=website`, `verified=false`), cached for at most a
day and never beyond the place-source record; they do not create permanent venue aliases.

Booking-provider hosts stay behind their adapters; website discovery cannot bypass their pinned
operations or budgets. A source website already naming a supported booking venue is recognized
without fetching it. Website reads use a separate credential-free client, a 512 kB decoded-body cap and a five-second
optional-enrichment deadline. HTTPS, public DNS and exact-host redirects are enforced. All sites
share 50 reads per day, half per person, paced one second apart; redirects count too. Failed reads
back off for 15 minutes, and refusals share a persistent pause. Directory results survive discovery
failure. `dine_status` reports the feature's aggregate budget and pause without reading websites.
Disable it and restart to remove cached discovery data. Website requests omit tracking parameters and fragments; source URLs with other queries and
redirects carrying queries are refused. Discovery stores only query-free recognized booking links,
with no raw HTML or cookies. Original place data follows its usual retention policy.

Authentication, operator actions, revocation and shared data are documented in
[docs/PERMISSIONS.md](docs/PERMISSIONS.md).

## Use as a library

```python
from dine_mcp.composition import build_app

app = build_app()
found = await app.service.find_venue("me", name="Harbour Grill", area="Raffles Place", country="SG")
venue_id = found["venues"][0]["venue_id"]
result = await app.service.check_availability("me", [venue_id], "2026-11-06", party_size=4)
```

The tool surface follows SemVer; the Python API is not yet stable (0.x).

## How it works

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains venue resolution, the provider protocol,
the request budget and the safety boundary; [docs/PROVIDERS.md](docs/PROVIDERS.md) lists each
provider's endpoints, limits and quirks; [docs/ROADMAP.md](docs/ROADMAP.md) what comes next.

## Verification

`make check` runs offline safety, contract, real stdio/HTTP, isolation and regression checks.
`make qualify` additionally installs pinned Node 22+ tools and runs Inspector plus the applicable
generic MCP conformance scenarios. See [verification](docs/VERIFICATION.md) for scope, package
checks and the separately gated operator canaries. Health checks never contact providers.

## Licence

Apache-2.0. Prior art and dependencies are credited in [NOTICE](NOTICE).

## Optional availability grid

Set `DINE_APPS=1` to advertise `ui://dine/availability.html` to MCP Apps hosts.
The bundled view shows venues against venue-local service dates, including after-midnight slots,
observation timestamps and unknown coverage. Filters run locally. Booking-page navigation asks the
host to open an HTTPS link only after a click; the view never calls providers or tools itself.
Clients without Apps continue to receive the text and structured result. The option defaults off.

Development needs Node 22+ for `make ui` (rebuild) and `make ui-check` (locked dependencies,
reproducible bundle/notices and offline rendering/SDK tests). Python installations need no Node.
See [roadmap](docs/ROADMAP.md) for the remaining provider and connection prerequisites.
