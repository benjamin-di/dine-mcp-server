# Static provider contract review

Reviewed 2026-10-29. This is source research, not live availability acceptance or a permission
finding. No new-provider availability request or login was run. All new adapters are experimental,
disabled by default and have no `last_verified`. Review current provider/venue terms and obtain
any required permission before enablement. Terms or a source client's behaviour do not confer
permission to bypass a challenge, automate login or access an account.

| Source | Pinned evidence | Decision |
|---|---|---|
| [kvendrik/zenchef](https://github.com/kvendrik/zenchef/tree/d324cc9a97196b528ad66824ae406b6fc5f792e7) | `src/zenchef-api.ts`, `src/api.ts`, `src/types.ts`, `src/commands/availability.ts`, `src/date.ts`, `src/scraper.ts` | Independent implementations of the documented guest reads only. Do not copy its auth, booking, payment, waitlist-enrolment or asset-crawling features. Zenchef missing dates and offer requirements remain unknown; Formitable time-sensitive product discovery is partial. |
| [Formitable public SDK](https://cdn.formitable.com/sdk/v1/ft.sdk.min.js) | Reviewed static asset: `/api/restaurant/<uid>/status`, `live`, optional `zenchefId`, widget configuration and `/side/<language>/<uid>/` | Status verifies a live widget; it does not publish a restaurant name/time zone. Migrated widgets are refused, not silently relabelled as another provider. This unversioned source must be rechecked before enablement. |
| [printing-press-library](https://github.com/mvanhorn/printing-press-library/tree/695dff9335ffaba94ba2d3aa14940bf568bc4cad) | Tabelog `internal/source/client.go`, `parser.go`, `discovery.go`; TableCheck `internal/planner/*`; Omakase `library/travel/omakase/internal/omakase/*` | Tabelog suggestions and canonical detail facts support listing discovery. TableCheck `/v2/shop_search` is geographic, not a verified name-search fallback; no invented text parameter. Omakase public planning explicitly leaves exact date/party seats unknown. |
| [tellang/catchtable-cli](https://github.com/tellang/catchtable-cli/tree/5cae099fd0766aa4ecb137ecc2e495c4dea6c6b2) | `client.py`, `config.py`, `models.py`, `commands/search.py`, `commands/shop.py` under `catchtable_cli/` | Catalogue search/detail only on `ct-api.catchtable.co.kr`; its README's different default API hostname is not used. No cookie, curl impersonation/fallback, account/reservation list, seat read or booking endpoint. Guest acceptance is unverified. |
| [Tablebooker PHP API library](https://github.com/tablebooker/tablebooker-php) | Public partner-client description | Does not establish an anonymous guest seat contract; no adapter endpoint added. |

OpenRice seat endpoints and Omakase authenticated inventory/account-identity contracts were not
established by the reviewed sources. They remain explicit prerequisites, not fabricated routes.
Tabelog/CatchTable catalogue facts and bookable-day summaries do not prove date/party seats.

Fixtures are independently invented and contain no real accounts, seats, browser state or copied
provider payloads. Every admitted host/method/path is recorded in the adapter's `operations` and
validated by the shared transport and safety suite. Static endpoint evidence never sets a live
verification date. See [provider behaviour](PROVIDERS.md) and [roadmap](ROADMAP.md).
