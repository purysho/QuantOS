# Contributing

Thank you for helping. QuantOS is Apache-2.0 licensed. By submitting a contribution you agree that it is licensed under the same terms (Apache-2.0, section 5), and that you have the right to submit it.

## Ground rules

These rules are what make the project trustworthy. Pull requests that weaken them will not be merged.

1. **Point in time.** Every fact carries event time *and* knowledge time. Nothing may use information that was not known at the decision time.
2. **Fail closed.** Missing, ambiguous or inconsistent input raises or is reported. It is never guessed, filled or silently skipped.
3. **Content-addressed identity.** Artifacts are immutable and identified by a hash of their canonical content.
4. **Independent validation.** An external engine (QuantLib, NautilusTrader, ORE, a data provider) sits behind a QuantOS contract and is checked against an independent reference. Mismatches are preserved, never tolerated.
5. **No live-capital path.** No code may place orders, hold broker credentials or grant external-order authority. Every artifact's authority stays `NONE`.

## Workflow

```bash
uv sync --locked --extra ore           # exact environment (ore: x86-64 Linux/Windows)
uv run python -m unittest discover -s tests
uv run quantos demo && uv run quantos edge-demo
```

- One behavior per change, with tests that prove it, including the failure cases.
- If you change dependencies, run `uv lock`, then `uv export --frozen --no-emit-project --no-dev -o requirements.lock`, and review the new licenses in `quantos.license_policy`. Regenerate `THIRD_PARTY_NOTICES.md` with `python -m quantos.license_policy > THIRD_PARTY_NOTICES.md`.
- Every stored table must map to a terminal workspace (`quantos.terminal.WORKSPACES`); a test enforces this.
- New network destinations must be added to the egress allowlist (`quantos.security.DEFAULT_EGRESS_ALLOWLIST`) with a reason.
- Document a new stage in `docs/` and record it in `HANDOFF.md`.

CI runs the full suite on Python 3.11, 3.12 and 3.13, and a pull request must be green on all three.
