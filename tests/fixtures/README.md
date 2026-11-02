# Fixtures

Synthetic responses shaped like the providers' public widget APIs as observed on 2026-10-29
(field names and structure kept; venues, ids, keys, prices and texts invented). `D0`, `D1`, … are
replaced by dates relative to today when a test loads them. Venue texts include prompt-injection
bait on purpose: tests check it never reaches a result as an instruction.

`../../surface.json` is the generated interface snapshot: review its diff like code.
Run `python scripts/surface.py update` explicitly; checks never create a missing baseline.
