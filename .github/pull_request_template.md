## Summary

- What changed:
- Why it changed:
- User/developer impact:

## Safety review

- [ ] No unrestricted shell/model execution was introduced.
- [ ] New or changed tools have strict schemas, risk classification, confirmation behavior, and tests.
- [ ] Secrets and private content are redacted and are not persisted unintentionally.
- [ ] Local API changes remain loopback-only and authenticated.

## Validation

- [ ] Ruff
- [ ] Pytest
- [ ] ESLint
- [ ] Vitest
- [ ] TypeScript/Vite build
- [ ] Cargo fmt/check
- [ ] Manual Windows hardware/tray smoke test, or limitation documented
