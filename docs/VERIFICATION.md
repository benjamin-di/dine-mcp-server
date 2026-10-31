# Verification

## Offline gate

`make bootstrap && make check` runs lint, formatting, mypy, runtime-lock consistency, the
locked optional App build/notices/SDK gate and pytest. Development requires Node 22+.
The suite uses synthetic fixtures and fake place sources. It includes cache/coverage regressions,
partial results across pushback/deadlines, redirect accounting, breaker queueing, streaming caps,
DNS filtering, HTTP authority/body refusal, identity, expiry, scheduling, logs and date boundaries.
Website-discovery regressions use synthetic HTML and the real service/network policy: opt-in
and disable guards, source ownership, malformed URLs and IP literals, private/mixed DNS answers,
redirect escapes, provider-operation/budget bypass refusal, per-hop pacing/accounting, dispatch caps, credential/trace refusal, streaming
limits, MIME/refusal fallback, ambiguous widgets, concurrent coalescing, expiry, source changes,
restart reuse and provisional-alias isolation. The detected channels and offline budget status
also cross the MCP boundary. The pre-roadmap audit gate had 212 passing offline tests. No default test
fetches a real website. Packaged stdio qualification also enables discovery and verifies offline
status with zero website reads, inside a container without external networking.

The follow-up audit adds 33 offline cases for TableCheck point queries/caveats, cached-rule
planning, provider provenance after place expiry/refresh, rich-detail retention, bounded metadata,
policy-error propagation, adapter isolation, malformed hours/URLs, empty kinds, local timestamps,
missing time zones, website source races and lock deadlines, and concurrent standalone token
issuance/identity validation. These audit fixes preserved the tool surface. The historical model/container
results below predate that audit; fresh roadmap evidence is distinguished below.

The 2026-10-30 parsing audit adds 41 offline cases. They cover actual DST gaps/folds in the three
default adapters, offset-disambiguated TableCheck timestamps, malformed calendar shapes/states,
positive-slot retention without complete negative coverage, negative-cache avoidance, explicit
empty-day controls, mismatched slot dates, invalid release clocks and strict CLI/Python HH:MM
queries. All provider responses are synthetic; these cases do not refresh live verification dates.

Real subprocess stdio and loopback TCP HTTP use a synthetic application with refused network reads.
Requests and progress are checked over stdio; stateless JSON HTTP has final responses only.

The gate also checks the generated provider matrix, fixture privacy and documentation/surface
agreement. Update the tool snapshot deliberately when a schema or description changes. The new
optional `dine_status(check=false)` input keeps default status offline.

## Protocol qualification

With Node 22+ and npm available, run `make qualify`. The lock pins Inspector 2.13.0 and conformance
0.1.16. The script starts only a loopback synthetic server, tears it down in finally, and saves
reports in `.artifacts/protocol/`:

- Inspector tools/list over stdio and dine_status over HTTP.
- Generic conformance: server-initialize, ping, tools-list, logging-set-level and
  dns-rebinding-protection. `tools/qualification/expected-failures.yaml` has no exceptions.

This is an applicable-scenario qualification, not a full upstream demo-server suite. Other
scenarios require named demonstration tools, resources, elicitation, sampling or stateful SSE
that the default four-tool Dine surface does not expose. The optional App resource is tested
separately through FastMCP and the bundled JavaScript SDK. Authentication/identity and Host/Origin denial run separately against
the real authenticated ASGI app; the conformance fixture has no real accounts or provider access.

## Roadmap increment acceptance

Offline regressions cover cached/uncached minimum-party shortcuts through MCP, empty slot-kind
refusal, experimental default-off policy, pinned guest reads, request bounds, verified provider
name discovery, country filtering, missing/malformed dates, explicit waitlists, retained partial
tickets, DST gaps/folds, widget identifiers and unknown/refused venues. No new-provider live
acceptance, login, secret write, deployment or publication is implied.

`make ui-check` installs from the npm lock without lifecycle scripts, rebuilds the page and licence
notices in memory, and requires byte equality with the shipped files. Five offline JavaScript cases
cover text injection, unsafe URLs, filters, overnight/unknown coverage, party limits/contact and
the actual bundled SDK's initialization → tool-result → explicit host link flow. The page makes
no fetches or automatic tool calls. This is a synthetic DOM host check, not real-browser acceptance.
The available collaborative browser could not start because its host libraries are missing.

Inspector stdio/HTTP and the five applicable generic conformance scenarios were rerun successfully
for this increment. The isolated wheel contains the exact compiled App and notices. Container
qualification has not been repeated here; this workstation has no Docker executable.

## Standalone artifacts

Release 0.2.3 was built from its sanitized public tag. Anonymous PyPI wheel/sdist downloads
matched the accepted build hashes, and fresh wheel and lock-free PyPI installations passed
offline stdio discovery/status and packaged App resource/notices checks. Both image architectures'
source/version/revision labels were verified anonymously; a native amd64 pull passed offline
stdio initialization and missing-bearer HTTP refusal. Arm64 runtime execution and real App-host
acceptance are not established by these checks.

`uv build --wheel` builds a candidate. Smoke-test it in an isolated environment with places
disabled and a fresh private state directory, for example:

```sh
DINE_PLACES=none DINE_STATE_DIR=/tmp/dine-wheel-smoke \
  uv run --offline --isolated --no-project --with ./dist/dine_mcp-0.2.6-py3-none-any.whl dine-mcp status
```

The `--offline` example needs cached dependency metadata/wheels; omit that flag for an initial
package install. The status command with `DINE_PLACES=none` performs no provider reads.

On a Docker-capable host, qualify an exact clean source checkout:

```sh
SOURCE_REVISION="$(git rev-parse HEAD)" ./scripts/qualify-container.sh
```

For an archive, supply its known full source commit as `SOURCE_REVISION` instead.
The script renders Compose, builds temporary dependency/application images, checks OCI revision
and version labels, and exercises the installed package over stdio and authenticated HTTP. Runtime
containers have no external network, published ports or persistent state, and use a read-only root,
uid 1000, dropped capabilities and temporary state. It checks refusal without a bearer and denial
of an untrusted Host, then removes its own containers and image tags. Builds need network access
for pinned base images and packages. It never runs Compose `up` or pushes an image.
This does not verify registry provenance, a clean published-image pull or a production route.

## Operator-only provider checks

Offline SevenRooms regressions resolve a synthetic venue before rejecting its range request.
They check the MCP text and structured result, unchecked dates, no false negative cache entries,
and retention of an earlier successful batch. HTTP 400 alone does not prove an unknown venue;
the exact upstream reason for a live rejection requires separate evidence.

No default test contacts a provider. The live tier is export-ignored and double-gated:

```sh
DINE_LIVE_READS=1 DINE_STATE_DIR=/private/persistent/dine-state \
  uv run pytest -m live tests/live
```

Each eligible provider makes one ordinary paced metadata request and checks its widget contract.
Do not loop it. Budgets, breakers and the 15-minute monitoring cooldown use persistent state.
`dine_status(check=true)` or `dine-mcp status --check` additionally checks tomorrow's 18:00–20:00
availability (normally one metadata and one availability read per provider). These checks report
structure/coverage, not a promise that slots exist. Failures remain per-provider; health stays offline.

An operator scheduler can invoke `uv run python scripts/drift-report.py` with persistent state.
It prints JSON and exits 0 for current healthy checks, 1 for a provider failure and 2 when a
cooldown prevented obtaining fresh evidence after restart. No schedule is installed automatically.

## Model evaluations

`uv run python evals/run.py --model <configured-model> --all --runs 1` runs all synthetic
tasks through the Claude Code CLI and records answers, turns, tokens and failures. The model must
be supplied explicitly by the calling environment. Use `--held-out` for the eight held-out tasks
or `--only <task-id> ...` for targeted development checks. Each run has fresh temporary state and
configuration, cleaned up afterwards. Timeouts, nonzero CLI exits, malformed responses and verifier
failures fail the run; any failed task makes the command exit 1 while retaining its JSON report.

The original 30 tasks include Japanese/Chinese names, exactly matching names in two countries,
WhatsApp-only contact, observations retained after mid-call pushback, and Tokyo overnight slots
for a person in another time zone. Deterministic regressions check those fixture distinctions.

On 2026-10-29 the expanded suite produced **28/30** under the original verifiers. Review found
two development-verifier false positives: asking for the country before searching is valid, and
mentioning an explicitly rejected candidate does not select it. Corrected verifiers accept all
**30/30** recorded answers (8 held out, unchanged), mean 3.97 turns. Both affected cases then passed
three fresh runs each (**6/6**). The original report, separate rescore and focused rerun are kept in
`evals/results/`; these are distinct from the earlier 24/24 and historical three-run 72/72 results.
Model tasks supplement deterministic regressions;
they do not establish network safety, production readiness or the correctness of every provider.

The roadmap increment adds a minimum-party scenario (31 total; the eight held-out tasks remain
unchanged). Its first fresh run was 29/31: the two Tokyo scenarios exposed a fixture calendar
that began tomorrow in Tokyo while the prompt's Singapore calendar still described Tokyo's
current day. The fixture now includes each venue's current local date; verifiers were unchanged.
A complete fresh rerun passed **31/31**, mean 3.94 turns. Both original and rerun reports are kept
separately, with deterministic midnight-boundary regression coverage. This is model acceptance
of the synthetic tool behaviour, not live provider acceptance.
