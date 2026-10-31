# Providers

What dine-mcp reads from each booking system, and its limits. Observed signed out, at human pace,
on 2026-10-29 for SevenRooms, TableCheck and ResDiary. Experimental additions below have
static contracts only, remain disabled by default and have not been probed live. Terms review
and any required permission must precede enablement. For the three existing adapters, dine-mcp reads only the public
widget a diner's own browser uses, for a person checking a table themselves, and never books.
The operator can lower any provider to `listing` (links only) without a release.

## SevenRooms (`public`, beta)

| | |
|---|---|
| Venue ref | the url key in `sevenrooms.com/{reservations,explore,experiences,app/reservations,landing}/<key>` |
| Venue | `GET www.sevenrooms.com/api-yoa/dining/venue_info?venue_url_key=<key>&widget_type=res_widget`: time zone, country, party limits (`min_guests`, `max_guests`), booking window (`maxDaysOut`, counting today) |
| Availability | `GET …/api-yoa/availability/ng/widget/range?venue&party_size&start_date&num_days≤3&halo_size_interval=100`: whole days, three per request |
| Slots | `type: book` with an access id → bookable; `type: request` → request. Experience title and the card-required flag per slot |
| Statuses | a closed day is `[]`; beyond the window and above the party maximum look like "sold out", so both come from the venue settings first; venue-info 404 means unknown venue. Range rejections (including HTTP 400) mean `unavailable`, with unobserved dates left unchecked; they do not prove the venue is unknown or sold out. |
| Link | `/explore/<key>/reservations/create/search/?date&party_size&start_time` (prefills all three) |
| Cost | 1 request per 3 days; responses can be a few MB at busy venues |
| Not read | prices (only in free text), seating area names, the hold and booking endpoints |

## TableCheck (`public`, beta)

| | |
|---|---|
| Venue ref | the slug in `tablecheck.com/[<locale>/]shops/<slug>/reserve` or `tablecheck.com/<locale>/<slug>/reserve…` |
| Venue | `GET www.tablecheck.com/en/shops/<slug>/reserve` (the page works for every venue, including those outside TableCheck's marketplace such as Singapore): shop id, time zone, country, currency, party maximum. Unknown slugs redirect to `/en/not-found` |
| Availability | `POST production.tablecheck.com/v2/hub/availability_calendar_v2 {shop_id, start_at, num_people}`: about two months of dates per request, but only the anchor time ±2 hours |
| Slots | bookable only (the calendar has no request or waitlist slots); no course, area or card flag |
| Statuses | `closed_dates` → closed. The booking window is not published: dates not yet released look full, and results say so |
| Link | `/en/shops/<slug>/reserve?start_date&num_people&start_time` (prefills all three) |
| Cost | 1 request per 4 hours of time window (17:00–22:30 is 2) |

## ResDiary (`public`, beta)

| | |
|---|---|
| Venue ref | the widget slug, plus `/<ThemeId>` from `booking.resdiary.com/widget/Standard/<Slug>/<ThemeId>`; or `resdiary.com/Restaurant/<Slug>` |
| Venue | `GET booking.resdiary.com/api/Restaurant/<slug>/Setup?date&channelCode=ONLINE`: party limits, days bookable ahead (sometimes unknown), areas, a Windows time zone name (mapped to IANA). Unknown or departed venues answer 500 |
| Availability | `POST …/AvailabilityForDateRange {DateFrom, DateTo, PartySize, ChannelCode}`: 7 days per request; dates without availability are left out |
| Slots | bookable, with the dining areas that have the table |
| Statuses | nothing available, closed, beyond the window and above the party maximum all look empty; the last two come from Setup first |
| Link | the widget with `?date&time&partySize` when the ThemeId is known; otherwise `www.resdiary.com/Restaurant/<Slug>?covers&bookingDate&bookingTime`, which redirects to the diner portal |
| Cost | 1 request per 7 days |

## Experimental additions (off by default)

| Provider | Reads and cost | Coverage and limits |
|---|---|---|
| Zenchef (`public`) | `GET bookings-middleware.zenchef.com/getWidgetParams?restaurantId`; `GET /getAvailabilities?restaurantId&date_begin&date_end`, one request per date | Numeric restaurant IDs; bookable and waitlist only when explicit. Missing dates, malformed shifts and unreviewed offer requirements remain unknown. No time zone in reviewed metadata; a fresh place-source zone is required. Links use `bookings.zenchef.com/results?rid=…`, with no advertised date/time/party prefills. |
| Formitable (`public`) | `GET widget-api.formitable.com/api/restaurant/<uid>/status`; three product searches and at most three ticket/day availability reads per date | Six availability requests per date estimated before dispatch. Searches are time-sensitive and not exhaustive: positive slots are partial, including after later failure, and empty results remain unknown. Migrated/non-live widgets are refused. Status publishes no venue name/zone; the explicit widget ID label is not a verified restaurant name. Prices, card requirements and exhaustive experience coverage stay unknown. |
| Tabelog (`listing`) | `GET tabelog.com/en/suggest/keyword_suggest?keyword`; at most five canonical English restaurant pages | Name discovery plus verified canonical identity and Restaurant JSON-LD. No exact seats or reservation eligibility inference. A search costs at most six reads before redirects. Japanese links are normalised to the same reviewed English venue path. |
| CatchTable Korea (`listing`) | `POST ct-api.catchtable.co.kr/api/v6/search/list` with keyword/paging (read query), then at most five `GET /api/v4/shops/<ref>` detail reads | Bounded Korean discovery with verified alias/ref binding and Korean venue time zone. No cookies, signed-in account reads, seats or reservations. Anonymous API acceptance is unverified; international CatchTable is a separate contract. |

Zenchef/Formitable clock-only slots in DST gaps or folds are refused rather than assigned a guessed
offset. Source refs become pinned adapter paths; source URLs never become arbitrary fetch targets.
Optional website discovery recognises reviewed Zenchef/Formitable widget configurations without
loading their JavaScript. It retains only the numeric Zenchef `rid` query, never other query tokens.

The [static review](PROVIDER-REVIEW.md) records exact sources, decisions and unresolved contracts.
The operator can explicitly enable an accepted experimental adapter with an overrides entry such
as `[providers.zenchef]` followed by `enabled = true`. This does not supply acceptance evidence.

## Not read (yet)

| Provider | What dine-mcp does | Prerequisite |
|---|---|---|
| OpenTable, Resy, Tock, TheFork, Quandoo, others | returns supplied booking links (`unsupported`) | Existing terms restrictions remain; no automated inventory reads added. |
| OpenRice Table Booking | supplied booking link | Identify and review the date/party read and status contract and terms/permission. |
| Omakase.in | supplied booking link | Reviewed authenticated inventory and account-identity reads before the [Connections design](CONNECTIONS.md) can be implemented. |
| Tablebooker | supplied booking link | Review a guest inventory contract; partner API credentials are not a public widget contract. |

## Adding one

See [CONTRIBUTING.md](../CONTRIBUTING.md). A provider starts `experimental` (off unless the operator
enables it), with synthetic fixtures for every status it can produce. Static research never sets
`last_verified`; that field requires recorded live acceptance.
