# Security Policy

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub's "Report a vulnerability" (Security → Advisories) on this repository, rather than in a public issue. Include the affected version, the steps to reproduce, and the impact. You should get an acknowledgement within a week.

## Scope and design

QuantOS is research software with **no live-capital path**. Security controls that are in scope:

- **Secrets:** API keys come from `QUANTOS_SECRET_<NAME>` or a private secrets directory. Group- or world-readable secret files are refused. Secrets are redacted from logs and exports and are never placed in URLs.
- **Egress:** every outbound request passes a host allowlist and the operator kill switch (`quantos kill-switch`).
- **Audit:** operator actions are recorded in a hash-chained audit log (`quantos audit-verify`).
- **Terminal:** it binds to loopback only, accepts GET only, sends a strict CSP, pins Perspective with SRI, and verifies each table's SHA-256 in the browser.
- **Parsing:** feeds are parsed with DOCTYPE/entity XML refused, size limits, and no redirects.
- **Live USB:** persistence is LUKS-encrypted.

Out of scope: vulnerabilities in third-party data providers and in upstream packages (please report those upstream), and deliberately misconfigured deployments (for example, exposing the terminal beyond loopback through a proxy).
