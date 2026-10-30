# Security

Please report vulnerabilities privately: use GitHub's "Report a vulnerability" (Security tab →
Advisories) on this repository rather than a public issue.

In scope: anything that lets dine-mcp send a request outside a provider's pinned read operations
or to a host the model chose; makes it book, hold, cancel or change anything; lets one person act as
another or see another's data; serves `/mcp` without a valid token; leaks a token or the state
directory; or makes a tool result carry exception text or provider-controlled instructions.

A provider changing its booking widget is not a vulnerability; open a normal issue.

Only the latest release is supported.
