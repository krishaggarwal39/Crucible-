# ADR-003: TOCTOU-Safe SSRF Protection for Target Agent Connector

## Status
Accepted

## Context
Crucible connects to user-provided URLs (target agent endpoints) to simulate interactions. In a multi-tenant SaaS, a malicious user could register an agent with a URL pointing to internal infrastructure (e.g., `http://169.254.169.254/latest/meta-data/` on AWS, or `http://localhost:6379/` to reach Redis).

This is Server-Side Request Forgery (SSRF) — a critical vulnerability (OWASP Top 10).

The naive fix (resolve DNS → check if IP is private → make request) has a Time-of-Check-to-Time-of-Use (TOCTOU) flaw: the attacker can use DNS rebinding to return a public IP during the check, then switch DNS to a private IP before the actual connection.

Options considered:
1. **URL blocklist** — reject known-bad patterns (169.254.x.x, 10.x.x.x, etc.)
2. **DNS check then connect** — resolve, validate, then make request (TOCTOU vulnerable)
3. **Custom network backend** — resolve DNS once, validate, connect directly to the validated IP

## Decision
Use a **custom httpcore network backend** (`SSRFSafeBackend`) that resolves DNS, validates the IP, and connects to the validated IP in a single atomic operation — eliminating the TOCTOU window.

## Rationale

**Why not a URL blocklist?**
- Doesn't prevent DNS rebinding
- Doesn't handle IPv6 mapped addresses, link-local, or obscure private ranges
- Easy to bypass with creative encoding (decimal IPs, hex notation, etc.)

**Why not resolve-then-connect (two-step)?**
- Classic TOCTOU: attacker's DNS returns `1.2.3.4` (public) during validation
- Between validation and actual connection, DNS TTL expires
- Second resolution returns `10.0.0.1` (internal) — SSRF succeeds
- Timing window is small but exploitable with short TTL records

**Why custom network backend?**
- `SSRFSafeBackend.connect_tcp()` performs: resolve → validate → connect **atomically**
- The IP used for validation IS the IP connected to — no second resolution
- Built on httpcore's extension point (network backend), so it works with httpx connection pooling
- Private IP check uses Python's `ipaddress` module which handles all edge cases (IPv4-mapped IPv6, loopback, link-local, etc.)
- In development mode, private IPs are allowed (needed for localhost testing). This is a
  deliberate trade-off, not an oversight: `SSRFSafeBackend` could not reach localhost at
  all when it was first enabled everywhere (commit d7a7f47), so it is gated on
  `settings.ssrf_protection_active`. That resolves to on outside development, and can be
  forced on anywhere with `SSRF_PROTECTION_ENABLED=true`
- Every resolved address is validated, not just the first, so a hostname returning a mix of
  public and private records cannot smuggle a private target through
- IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`) are unwrapped
  before validation, along with reserved, multicast and unspecified ranges

**Additional protections layered on top:**
- HTTPS-only enforcement in production (prevents plaintext sniffing)
- Redis-backed circuit breaker (4 consecutive 5xx → trip) prevents abuse
- Connection timeout of 5 seconds prevents slow-loris style attacks
- 30-second read timeout prevents hanging connections

## Consequences
- **Positive**: Eliminates SSRF including DNS rebinding attacks
- **Positive**: Zero-config in development (private IPs allowed when APP_ENV=development)
- **Positive**: Works with httpx's async connection pooling — no performance penalty
- **Negative**: Relies on httpcore internal API (`_pool._network_backend`) which could change
- **Negative**: Adds DNS resolution latency on first connection (subsequent connections are pooled)
- **Negative**: Because it is off in development, the protection is not exercised by normal
  local use. `SSRF_PROTECTION_ENABLED=true` exists so it can be tested deliberately
- **Mitigation**: `httpx` and `httpcore` are declared dependencies (`httpcore>=1.0,<2.0`),
  and `tests/test_connectors/test_ssrf.py` covers the address policy, the resolve-then-connect
  behaviour, and — importantly — asserts that `SSRFSafeBackend.connect_tcp` still accepts
  every keyword argument httpcore passes it. That last test is what would catch an httpcore
  upgrade breaking every outbound request in production

## Note on TLS
Connecting to a validated IP does **not** weaken certificate verification. httpcore calls
`start_tls(server_hostname=...)` with the original origin host, not the address handed to
`connect_tcp`, so SNI and hostname verification still happen against the real hostname.
(Verified against `httpcore/_async/connection.py`.)
