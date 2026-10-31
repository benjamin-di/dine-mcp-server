# Roadmap

Updated for the 0.2.4 source fixes. This describes the current
source and remaining qualification work. Scope remains **availability only**: no booking, holds,
cancellation or watches.

## Built in current source

- Four read tools: find, get, availability and status, with output schemas, text mirrors,
  classified errors, read-only annotations and a reviewed surface snapshot/byte budget.
- SevenRooms, TableCheck and ResDiary guest adapters; Google place identity, booking-link
  detection, curated channels and stable venue ids/aliases.
- Coverage-aware cache reuse, including separate TableCheck time windows and honest missing
  coverage. Completed batches and partial positive slots survive later errors and deadlines.
- SevenRooms range rejections remain `unavailable`, preserving completed observations without
  treating unchecked dates as sold out or a resolved venue as unknown. Assistant guidance asks
  for missing dates/diner count and uses status for diagnostics rather than restaurant searches.
- One deadline across lookup, planning and checking; estimated and actual 20-request caps;
  individually paced/charged redirects, restart-safe budgets/breakers and bounded Retry-After.
- Strict configurable HTTP Host/Origin protection, inbound and streamed response limits, and
  public-only resolved provider addresses connected without a second DNS lookup.
- First-contact gateway records, standalone hashed user tokens, single-user HTTP, stdio, CLI and
  Python. Global disable covers every venue entry point. Google channels and cached rows expire.
- Fair per-person provider scheduling with cancellation cleanup; joined completion logs and trace
  context propagation; progress notifications over stdio.
- Offline status plus optional budgeted canaries with a durable cooldown; operator-only live tier
  and scheduler-ready drift-report entry point. No live schedule is installed by this source.
- Conservative closed-day shortcuts from dated place hours, generated provider matrix, hardened
  Compose example and OCI version metadata.
- Bundled public region definitions for SG, JP, GB, AU and KR.
- Targeted model scenarios and deterministic fixtures for non-Latin names, cross-country
  ambiguity, messaging-only contact, partial pushback and overnight time zones. Ambiguous venue
  choices require clarification; a person's location alone does not select a venue country.
- Opt-in venue website widget discovery with source ownership checks, public-only DNS, exact-host
  redirects, bounded credential-free reads and expiring provisional links; no crawling or JavaScript.
- Temporary packaged-container qualification with no published ports or provider access.
- Follow-up audit fixes: single-time TableCheck anchors and retained caveats; cached-rule planning;
  provider-channel provenance across Google expiry/refresh; rich detail-cache preservation;
  bounded metadata; malformed input/adapter isolation; explicit time zones; website publication
  races/deadlines; and serialized standalone token issuance with unique token identities.
- Calendar parsing rejects malformed states, invalid clocks and ambiguous/nonexistent local DST
  times in the default adapters. Valid positive slots survive incomplete days without turning
  missing inventory into a cached negative. Explicit timestamp offsets can resolve DST folds.
  Invalid/ambiguous release times stay unknown; CLI/Python query clocks follow the same HH:MM
  contract as MCP. Signed-in guidance no longer refers to an unavailable connection tool.
- Real subprocess stdio/TCP HTTP regressions; pinned Inspector and applicable generic conformance
  scenarios with an empty expected-failures baseline. See [verification](VERIFICATION.md).
- Expanded model eval: 30/30 recorded answers pass corrected development verifiers (originally
  28/30 with two false positives); both affected cases pass three fresh runs each. Eight held-out
  tasks stay unchanged. Earlier 24/24 and three-run 72/72 evidence remain separate.

## Artifact and live qualification

The earlier audited fixes are included in the published 0.2.3 artifacts. Public release tests,
fresh package installs, anonymous image provenance checks for both architectures and an
offline amd64 image smoke passed. These checks do not qualify every provider, region or
optional feature. Provider canaries and remote authentication/runtime acceptance need a
supported operator environment and retain their own scope; local checks alone establish
neither a rollout nor live provider compatibility.

The conformance automation deliberately selects generic scenarios applicable to a four-tool
stateless JSON server. Upstream's named demo-tool, resource, elicitation and stateful SSE scenarios
are not claimed as implemented. Future protocol/dependency changes require reviewing this scope.

## Implemented optional capabilities

- **Minimum-party handling:** cached limits affect planning; undersized parties return
  `party_too_small`, `min_party`, the booking link and venue contact without a calendar read.
  An explicit empty slot-kind filter is refused at the MCP boundary.
- **Provider discovery:** reviewed Tabelog and Korean CatchTable name searches can supplement
  an unavailable or empty place source. They verify bounded venue details, retain ambiguity,
  filter the requested country and never fetch source-supplied arbitrary URLs.
- **Experimental availability:** separate Zenchef and Formitable guest adapters, pinned reads,
  synthetic fixtures, upfront request estimates and retained partial positives. Both need a fresh
  place-source time zone when widget metadata has none. Formitable experience discovery is
  deliberately partial; absent experiences or slots stay unknown. All four additions default off.
- **Regions:** Korea configuration with optional CatchTable discovery. This is provider discovery,
  not a general replacement for Google's Korean map/place coverage.
- **Optional MCP App:** `DINE_APPS=1` exposes a venue × service-day grid, local filters and explicit
  host-mediated booking links. The unchanged text result remains available. The wheel contains
  the built page and dependency notices; the development gate checks reproducibility and the SDK
  handshake against a synthetic host. Real-browser/host acceptance remains external qualification.

## Remaining code, with explicit prerequisites

| Work | What must be established before implementation |
|---|---|
| Omakase.in signed-in availability and Connections | A reviewed authenticated seat-read contract, a read-only provider-account identity check and current terms/permission decision. The reviewed public client explicitly cannot evaluate exact seats. [Connection design](CONNECTIONS.md) records the isolation, identity, fencing and acceptance requirements; no connection tool/page/session capture is shipped. |
| Tablebooker | A reviewed guest inventory contract. Its public partner API library alone does not establish a credential-free diner endpoint; it must not be treated as the Zenchef or Formitable API. |
| OpenRice Table Booking | A reviewed date/party availability request and response contract, including status meanings and terms/permission. Current source returns supplied booking links only. |
| Tabelog and CatchTable seat inventory | Discovery is implemented as listing only. Exact seats need separate reviewed status/coverage and authentication contracts; catalogue eligibility and day summaries are insufficient. International CatchTable is also a separate contract. |
| Full Formitable coverage, offers and widget migrations | Exhaustive experience discovery, offer eligibility/prices and explicit migration identity. Bounded positive observations are already implemented; omitted experiences remain unknown. |
| Further regions and Korean place identity | Add requested regions and a reviewed independent place-source integration when needed. CatchTable discovery is not general map identity. |
| FastMCP security update | The current public Maps dependency pins FastMCP 4.0.10. A compatible public Maps release is needed to adopt the advisory's patched FastMCP floor (4.1.2); update both locks and repeat protocol/HTTP qualification together. Filelock is already patched. |

The experimental providers require current terms/permission review and live acceptance before
operational enablement. Static endpoint research is recorded in [provider review](PROVIDER-REVIEW.md);
no canary, login, credential capture or availability probe was run for these additions.

## Out of scope

Booking, holds, cancellations and availability watches require a new major design with consent,
rechecks, no replay and an audit trail. They are not part of this implementation.
