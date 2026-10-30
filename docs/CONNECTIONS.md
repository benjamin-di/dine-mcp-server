# Signed-in provider connections: pending contract

No signed-in provider, connection tool, browser connection page, cookie import or login runtime
is shipped. Guest adapters never sign in. Omakase.in remains a booking-link handoff until its
authenticated inventory and read-only account-identity contracts are reviewed. Its reviewed public
planning client explicitly cannot evaluate exact seats; a successful page load or cookie alone
cannot prove a connected diner identity. This document defines the remaining implementation and
acceptance work; it is not a configuration guide for an existing feature.

The intended human sign-in boundary is the public
[signin-window](https://pypi.org/project/signin-window/) library. A person signs in directly on the
provider's page. Passwords and codes never pass through the model, serving process or logs.
No login form is filled automatically, no challenge is solved and no session is silently renewed.

Before this can ship, implement and verify all of these together:

- A separately composed human browser worker and fixed Connections URL. The serving process can
  read existing scoped session authority only; it cannot create a login attempt or sign in.
- Human OIDC browser authentication. Bind a connection owner to verified issuer/subject plus an
  administrator-owned immutable person claim, matching MCP's person identity. A copied link,
  backend bearer, model argument or spoofed person header cannot authorise browser actions.
- Same-origin POST/CSRF checks, bounded private browser sessions and fresh identity-provider
  authentication for Start or replacement. A model-visible connect tool returns only the fixed
  Connections URL, never a Live View capability or provider cookies.
- Private per-person state under `/state/<person>/` (directories 0700, files 0600), verified
  provider-account identity, durable disconnect fence and monotonic session generations.
  Disconnect/replacement invalidates pending adoption; a late completion cannot resurrect it.
- Serving-time admission checks the current generation and disconnect fence before each provider
  read. An already admitted call may finish; future calls refuse. Authentication failure marks
  sign-in needed, without retries or automatic re-enrolment.
- Exact-host, operation-scoped session injection at the shared transport boundary. No adapter
  receives arbitrary credentials and no shared HTTP cookie jar can mix people. Redirects are
  checked and charged normally; an authenticated read cannot redirect credentials elsewhere.
- Personal metadata and availability caches keyed by person/provider/session generation, separate
  from public observations. Disconnect/replacement prevents stale personal results being served.
- Synthetic acceptance for two-person isolation, wrong-account replacement, stale or absent
  identity, origin denial, spoofed headers, copied links, disconnect/adoption races, per-hop
  admission, credential leakage, expired sessions and restart recovery. Then operator acceptance
  against the reviewed provider contracts, without booking or revealing credentials.

There is no session-upload shortcut while these prerequisites remain unresolved. Existing guest
metadata and availability stay in the shared public cache; no personal session data is stored there.
