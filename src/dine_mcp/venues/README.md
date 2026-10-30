# Curated venues

Facts Google Maps and the providers do not carry: venues that book only by WhatsApp or phone, booking
links Google lacks, release times. One `[[venue]]` table per venue, keyed by its Google place id:

```toml
[[venue]]
google_place_id = "ChIJ..."
source = "https://example.com/reservations (the venue's own page), checked 2026-10-29"
booking_note = "Bookings open on the 1st of each month at 10:00 for the following month."
channels = [
  { kind = "messaging", text = "WhatsApp +65 0000 0000" },
  { kind = "link", url = "https://www.sevenrooms.com/reservations/example" },
]
```

Rules: public facts from primary sources only (the venue or the provider), with `source`; never
copy Google Maps fields or other databases into these files; no personal data.
