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

## Follow-up contract review (2026-10-31)

Additional static research did not qualify another exact-inventory adapter. No availability,
account, holding, booking or payment request was dispatched.

- **CatchTable:** the official web build `20261029200325/assets/bootstrap.js`
  (SHA256 `8bdce295a0d5d06bf2bb20b151225642d1546d08f691c65003dd9c7b2825d3c1`)
  confirms `POST /api/reservation/v1/dining/time-slots`, but its current caller supplies
  `encryptedParamString`, `tableSeqs` and `c1`–`c4`, with a `slotEncPublicKey` from a separate
  key exchange. Results use `timeSlotMap` and `resp2`, with service-day rollover rules.
  The older independent client's plain date/party body is insufficient evidence for the
  current contract. Do not add that older request as a guest seat operation or imitate its
  fingerprint fallback. The current profile GET is visible, but its stable account identity
  and independent candidate validation still need review before any session is adopted.
- **Omakase:** re-read the pinned public client's `internal/omakase/{client,model,parse}.go`.
  Its local inventory is a restaurant catalogue; exact seats remain unknown. A single public
  homepage request returned HTTP 403, so live research stopped. No authenticated seat or
  stable provider-identity contract was established; Connections cannot claim support.
- **OpenRice:** the public client `main.54fbe051.js`
  (SHA256 `2f0d5cc030a962b8ad928384d735d6f0ec0f8390aa3dfec91a6a1073d20e08f6`)
  names separate availability, picker and widget GETs. Route names alone do not establish
  their effective host/prefix, request fields, response schema or guest authorization. The
  manifest-selected booking chunk request returned HTTP 403 and research stopped. No API
  operation was added from these incomplete declarations.
- **Tablebooker:** its public homepage redirects to Zenchef's application page. This does not
  prove that legacy Tablebooker venues have migrated or that a legacy widget can be queried
  through Zenchef. Keep explicit channel identity and the existing link handoff.

The implementation gate is an evidenced exact request/response contract, a current access
and terms decision, and synthetic coverage. Signed-in adoption additionally needs a stable
provider account identity and independent candidate verification. Endpoint names, successful
login pages, cookies, catalogue/day summaries and booking holds do not meet those gates.
