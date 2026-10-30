# Contributing

Issues and pull requests are welcome, especially a provider that changed (`unavailable` or
`blocked` where it used to work), a booking system you would like supported, or a region file.

## Before you open a pull request

```sh
make bootstrap   # uv sync --locked
make check       # ruff, format, mypy, lock consistency, App build/tests, pytest (offline)
```

Development requires Python 3.12+, uv and Node 22+ (for the bundled optional App).
`make ui` rebuilds the App; the Python runtime needs no Node.

[AGENTS.md](AGENTS.md) has the rules: tests never reach a provider, `src/dine_mcp/transport.py` is the
only network module, every provider request is a pinned read operation, and fixtures are synthetic
or scrubbed. Keep personal details (names, phone numbers, e-mail addresses, reservations) out of
code, docs and fixtures.

### Add a provider

1. `src/dine_mcp/provider_definitions/<key>.toml`: access mode, maturity (`experimental` first),
   countries, hosts, URL patterns, pacing, budget, cache lifetime, a terms note and a credit line,
   with dated evidence comments.
2. `src/dine_mcp/providers/<key>.py`: the `Provider` protocol; its `OPERATIONS` are GET reads (a POST
   only for a read endpoint that takes its query as a body, with the reason).
3. Synthetic fixtures under `tests/fixtures/<key>/` for: available, none available, not yet open,
   unknown venue, closed day, party above the maximum, and a challenge or refusal.
4. Register it in `src/dine_mcp/providers/__init__.py`; the conformance suite
   (`tests/test_providers.py`) then runs it.
5. A row in [docs/PROVIDERS.md](docs/PROVIDERS.md) stating limits, not promises.

### Add a region

`src/dine_mcp/regions/<cc>.toml` (ISO 3166-1 alpha-2, lower case): the providers that matter there
in order, locales, currency, time zones and dated notes. The code never branches on country.

### Curated venues

`src/dine_mcp/venues/*.toml` holds facts Google Maps and the providers lack (WhatsApp-only booking,
release times), keyed by Google place id, from primary sources only (the venue or the provider),
each with its `source`. Never copy Google Maps or other databases into it.

Security problems: see [SECURITY.md](SECURITY.md) instead of opening an issue.

## How changes land

Each release of this repository is exported from a private working repository as one
`Release X.Y.Z` commit, so pull requests are not merged here: the next release would replace
them. When a change fits, the maintainer applies it there, credits you in the release notes
and closes the pull request with a link to the release that contains it. Issues are welcome;
answers are best effort.
