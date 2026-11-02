# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) over the tool surface.

## [0.2.10] - 2026-11-02

_Security: first public release carrying the 0.2.9 patched dependencies and fixes; 0.2.7 to 0.2.9 were not published publicly._

### Security

- Publish the 0.2.9 requirement on FastMCP 4.1.4 and Maps 0.6.2 in the public package, resolving component authorization and OAuth issuer-binding advisories for public installations.

### Changed

- Describe only implementation and public qualification work in the roadmap.

### Upgrade

- Action required: upgrade to 0.2.10 for patched dependencies; no configuration migration.
- Rollback: compatible with 0.2.9; rolling back to 0.2.6 restores its affected FastMCP dependency.

## [0.2.9] - 2026-10-31

_Security: patched dependencies and availability fixes; 0.2.7 and 0.2.8 were not published publicly._

### Security

- Publish dependency security floors in wheel and source-distribution metadata so unlocked installations retain the patched minimum versions.
- Require FastMCP 4.1.4 and Maps 0.6.2 to resolve component authorization and OAuth issuer-binding advisories.

### Changed

- Publish the tested wheel and source distribution once and require the complete anonymous PyPI file set to match before MCP Registry publication.

### Fixed

- Count expired venue metadata in request estimates even when the availability calendar is cached, refusing over-budget calls before dispatch.
- Retain cached observations when a deadline interrupts venue metadata, queued checks or planning.
- Preserve provider caveats for partial and empty observations, including cached fallback after pushback.
- Preserve TableCheck timestamp precision instead of rounding slots into an exact-minute search.

### Upgrade

- Action required: upgrade to 0.2.9 for patched dependencies; no configuration migration.
- Rollback: compatible with 0.2.8; rolling back to 0.2.6 restores its affected FastMCP dependency.

## [0.2.8] - 2026-10-31

_Not published publicly: superseded by 0.2.9 before delivery._

### Security

- Require FastMCP 4.1.4 and Maps 0.6.2 to resolve component authorization and OAuth issuer-binding advisories.

### Changed

- Publish the tested wheel and source distribution once and require the complete anonymous PyPI file set to match before MCP Registry publication.

### Fixed

- Retain cached observations when a deadline interrupts venue metadata, queued checks or planning.
- Preserve provider caveats for partial and empty observations, including cached fallback after pushback.
- Preserve TableCheck timestamp precision instead of rounding slots into an exact-minute search.

### Upgrade

- Action required: upgrade to 0.2.8 for patched dependencies; no configuration migration.
- Rollback: data-compatible with 0.2.6, but restores its affected FastMCP dependency.

## [0.2.7] - 2026-10-31

_Not published publicly: export validation failed; use 0.2.8._

### Security

- Require FastMCP 4.1.4 and Maps 0.6.2 to resolve component authorization and OAuth issuer-binding advisories.

### Changed

- Publish the tested wheel and source distribution once and require the complete anonymous PyPI file set to match before MCP Registry publication.

### Fixed

- Retain cached observations when a deadline interrupts venue metadata, queued checks or planning.
- Preserve provider caveats for partial and empty observations, including cached fallback after pushback.
- Preserve TableCheck timestamp precision instead of rounding slots into an exact-minute search.

### Upgrade

- Action required: upgrade to 0.2.7 for patched dependencies; no configuration migration.
- Rollback: data-compatible with 0.2.6, but restores its affected FastMCP dependency.

## [0.2.6] - 2026-10-31

_Version 0.2.5 was not published after container qualification failed; this release includes its availability fix._

### Fixed

- Check SevenRooms two-day remainders and cache gaps with supported single-day requests and matching request budgets.

### Upgrade

- Action required: none.
- Rollback: compatible with 0.2.4 and 0.2.5.

## [0.2.5] - 2026-10-31

_Not published: container qualification failed; use 0.2.6._

### Fixed

- Split SevenRooms two-day ranges into supported single-day requests, including remainders created by cached or closed dates.
- Estimate request costs from the same range plan used for dispatch, preserving budgets and exact requested coverage.

### Upgrade

- Action required: none.
- Rollback: compatible with 0.2.4.

## [0.2.4] - 2026-10-31

### Fixed

- Report SevenRooms request rejections as `unavailable`, preserving unchecked dates and any earlier completed observations.
- Describe provider failures without claiming a venue disappeared.
- Ask for missing dates and diner count in `dine_check_availability` guidance and direct `dine_status` callers to the restaurant availability tools.

### Upgrade

- Action required: none.
- Rollback: compatible with 0.2.3.

## [0.2.3]

### Fixed

- Compressed provider responses are decoded once, preserving decoded body limits, retry headers
  and refusal classification instead of returning unavailable for valid gzip responses.

## [0.2.2]

### Fixed

- Region documentation uses ISO country codes consistently with the provider definitions.

## [0.2.1]

### Fixed

- Default provider adapters reject ambiguous/nonexistent local DST times and malformed calendar
  data without reporting false inventory or complete negative coverage; valid partial slots survive.
- TableCheck requires boolean availability states and keeps timestamps within their calendar day.
- Provider clock parsing and CLI/Python query validation no longer silently truncate unsupported times.
- Invalid or ambiguous booking release clocks remain unknown instead of failing the venue check.
- Signed-in provider guidance no longer suggests an unimplemented connection tool.
- PyPI and MCP Registry publication require successful checks of the same tagged source.
- The standalone Compose default selects the current version.
- File locking uses the patched Filelock 4.2.1 security floor.
- The partial-batch deadline regression expires the service timeout after the later dispatch starts,
  avoiding a race with worker scheduling.

## [0.2.0]

Initial availability-only release. Public distribution is pending.

### Added

- Tools `dine_find_venue`, `dine_get_venue`, `dine_check_availability` and `dine_status`.
- Providers SevenRooms, TableCheck and ResDiary (public guest widgets, no account).
- Experimental Zenchef and Formitable guest reads, and listing-only Tabelog/Korean CatchTable
  discovery. All four additions default off and have no live acceptance claim.
- Minimum-party preflight and `party_too_small`/`min_party` output with contact and booking link.
- Optional MCP Apps grid, local filters, bounded CSP, bundled dependency notices and offline SDK tests.
- Bounded provider-search fallback for empty and unavailable place-source results, plus Korea configuration.
- Venue identity and booking-provider detection from Google Maps through
  [google-maps-keyless-mcp](https://pypi.org/project/google-maps-keyless-mcp/).
- Regions SG, JP, GB and AU; curated venue facts; operator overrides.
- Gateway, standalone multi-user and single-user HTTP modes; stdio.
- MCP Registry metadata and package ownership marker; public issue forms and dependency updates.

### Fixed

- Explicit empty slot-kind filters are refused at the MCP boundary.
- Evaluation dates follow each venue's time zone across the Singapore/Tokyo midnight boundary.

- Coverage-aware TableCheck caching, honest missing dates and retained partial batches/slots.
- Per-dispatch budgets and redirect pacing; queued breaker checks and bounded Retry-After.
- End-to-end deadlines, complete venue disable guards and expiring Google-derived channels.
- Strict configurable HTTP authority/body protection and streamed provider response limits.
- First-contact gateway records and provider DNS address filtering.
- Clarification before selecting ambiguous venues across countries; caller location is insufficient.
- Single-time TableCheck queries use one anchor; unreleased-date caveats survive live/cache results.
- Cached booking rules avoid false budget refusals; missing-coverage warnings retain provider notes.
- Expired dispatch budgets report `skipped_deadline` while retaining completed observations.
- Verified provider channels survive Google expiry/refresh; sparse search rows preserve place details.
- Bounded metadata caching, isolated adapter failures, strict booking-link parsing and malformed-hours fallback.
- Venue-local timestamp parsing and refusal of ambiguous slots when no time zone is known.
- Website discovery rechecks source ownership, preserves the original expiry and bounds lock waits.
- Standalone token writes are serialized and atomic; duplicate token identities and excess users are refused.

### Added — hardening

- Optional `dine_status(check=false)` canaries, CLI `status --check`, persistent cooldown and
  operator drift probes; default status and health remain offline.
- Fair scheduling, joined completion logs, trace metadata and stdio progress notifications.
- Explicit closed-day shortcuts, generated provider matrix, hardened Compose example and OCI version.
- Offline real transport regressions and pinned Inspector/generic conformance qualification.
- Model scenarios for non-Latin names, cross-country ambiguity, messaging contact, partial
  pushback and overnight time zones; explicit model selection and failing evaluation exit codes.
- Packaged-container qualification using temporary images and isolated stdio/HTTP runtimes.
- Opt-in source-owned website widget discovery, with expiring provisional channels, bounded
  credential-free reads, SSRF protections and directory fallback; no JavaScript or crawling.

[0.2.4]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.4

[0.2.5]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.5

[0.2.6]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.6

[0.2.7]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.7

[0.2.9]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.9
[0.2.8]: https://github.com/benjamin-di/dine-mcp-server/releases/tag/v0.2.8
