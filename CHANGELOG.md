# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) over the tool surface.

## [Unreleased]

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
