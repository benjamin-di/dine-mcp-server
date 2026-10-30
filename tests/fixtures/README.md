# Fixtures

Synthetic responses shaped like the providers' public widget APIs as observed on 2026-10-29
(field names and structure kept; venues, ids, keys, prices and texts invented). `D0`, `D1`, … are
replaced by dates relative to today when a test loads them. Venue texts include prompt-injection
bait on purpose: tests check it never reaches a result as an instruction.

`tool_surface.json` is the golden snapshot of the tool definitions: review its diff like code.
