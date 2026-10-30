# Permissions and data sharing

dine-mcp checks public restaurant availability. Every MCP tool is read-only: no tool books,
holds, cancels, changes an account or manages another caller. A successful availability result
is an observation, never a reservation. There are no provider sign-ins in this release.

## Who can call

HTTP requires authentication on `/mcp`. The server refuses to start in single or gateway mode
without a bearer token of at least 32 characters. `/health` is an unauthenticated liveness
check and never contacts a provider. Use HTTPS at your reverse proxy for remote clients.

| Mode | Credential and identity | Access |
|---|---|---|
| stdio | The local process; no HTTP token | All four tools as `local` |
| single | A bearer from `MCP_BEARER_TOKEN` | All four tools as `owner` |
| users | A personal bearer whose SHA-256 appears in `DINE_USERS_FILE` | All four tools as the user selected by the token |
| gateway | A backend bearer held by the gateway, plus its configured person header | Venue tools for the valid person asserted by the gateway; a local record is created on first contact; `dine_status` also works without a person header |

The mode is inferred from `DINE_USERS_FILE`, then `DINE_PERSON_HEADER`, otherwise single-user.
`DINE_AUTH_MODE=users|gateway|single` selects it explicitly. HTTP always checks the bearer,
including for status. MCP tool discovery requires a valid bearer but does not require a
roster entry. Missing or malformed gateway identities receive `unknown_person` when using venue
tools; there is no fallback to an owner or another person.

A trusted gateway authenticates its clients, checks their access and replaces their identity
header (for example `X-Person`) with its verified person id. Only requests carrying the
backend bearer can assert that identity. Keep that bearer in the gateway: giving it to a
client gives that client the gateway's authority to assert people. User-supplied
headers do not select a person in standalone users mode; the token does.

The roster records first contact; it is not a second admission list. The gateway owns membership
and access checks. Removing a local record does not revoke gateway access: a subsequent authorized
call recreates it. Revoke membership or grants at the gateway/identity provider.

## Operator actions and revocation

The MCP tools cannot add people, create tokens or change server configuration. Those actions
require shell access as the server operator:

| Action | Operator procedure | When it applies |
|---|---|---|
| Admit/revoke a gateway person | Change admission at the authenticating gateway | According to its token/grant lifetime; an already running call can finish |
| Inspect/tidy local records | `dine-mcp people list` / `people remove <id>` | Record maintenance only; a future authorized call recreates a removed record |
| Issue a standalone token | `dine-mcp users add <id> --file users.toml` | After the server loads that users file at startup |
| Revoke or replace a standalone token | Remove that user's entire TOML table; restart; use `users add` for a new token | After restart |
| Rotate a single-user or gateway bearer | Replace `MCP_BEARER_TOKEN`; restart the server and update its caller | After restart |
| Lower provider access or disable providers | Edit `DINE_OVERRIDES`; restart | After restart; global disable refuses find/get/check before external work; status remains available |

`users add` prints a newly generated token once and stores only its hash. Send it to its
intended user through a private channel. The users file is loaded at startup, not watched.
Each token hash must identify exactly one user; duplicate hashes prevent startup. Additions are
serialized through a sibling `.lock` file and use a private temporary file with atomic replacement.
The command refuses an addition beyond 1,000 users before changing the file. Manual edits must
be coordinated with additions; the lock file contains no credentials and should not be removed
while an addition is running.
`MCP_BEARER_TOKEN` accepts a comma-separated rotation set; all listed tokens have the same
single-user or gateway authority. Remove an old token and restart to finish rotation.
Removing a gateway roster entry does not revoke the gateway bearer: authenticated status
and tool discovery remain available.

## What callers share

All callers on one server share public venue records, stable venue ids, public availability
observations, provider-wide budgets and provider pauses. A venue id is not an access token;
any authorized caller can use a known id. The availability cache contains no person field or
account session. Fresh observations are reused for two minutes; older observations can be
returned during provider pushback, with their observation time and cache origin marked.

A person's id selects their daily share of provider requests. It does not create a private
copy of restaurant data or a separate provider account. `dine_status` exposes aggregate
provider health and budget usage, never the roster or another person's individual usage.
Google-derived contact and booking channels expire with the seven-day place cache. Reads filter
expired channels; startup and periodic cleanup remove expired persisted data. There is no stored
personal booking history, diner profile or signed-in provider session.

Opt-in website discovery shares recognized public booking links and aggregate website budgets.
It reads only the current place-source website with no account credentials. Detected links remain
provisional, expire within one day and the source TTL, and create no permanent provider aliases.
Discovery retains no HTML, website cookies or booking-link query values. Disabling it and restarting removes the
cached discovery data and copied channels; the original place-source contact data follows its
usual retention policy.
Signed-in providers, per-person account storage and a Connections page require a later release.

## State and credentials

Treat the entire `DINE_STATE_DIR`, including SQLite journal files, as private. It contains
opaque person ids, optional display names entered by the operator, individual budget counters,
public venue data and provider pause state. On Unix, keep the state directory owned by the
server user with mode `0700`, and its database and users file with mode `0600`. The application
requests those modes; the operator must ensure the filesystem enforces them. Back up and
restore the directory with the same access controls.

Provider reads have no account credentials. Google venue resolution is signed out; its
local cache and consent state also belong inside the private state directory. Bearers belong
in private environment or secret configuration, never in prompts, committed client examples
or bug reports. Tool logs use hashed person ids and argument fingerprints instead of raw
arguments; protect logs and backups too.

Report suspected authentication or isolation failures privately as described in
[SECURITY.md](../SECURITY.md). Provider pacing, pinned read operations and response limits are
explained in [ARCHITECTURE.md](ARCHITECTURE.md).
