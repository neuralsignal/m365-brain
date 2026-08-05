# Groups, audit log, and JWT validation for the deployed service

Deferred by ADR 0010. Everything here is meaningless with one operator on one machine and
mandatory the moment there are two.

## Requirements

- **JWT validation** on every request to the service. Front-door token introspection where the
  token's audience makes JWKS verification impossible; JWKS verification where it does not.
- **Groups** — more than one user means an authorization decision, which means a subject the
  decision is about.
- **Audit log** — who dispatched which intent, when, and what the upstream returned. The outbox
  already carries a client-supplied idempotency key per intent; the audit record hangs off it.

## Acceptance Criteria

- [ ] A request with no token, an expired token, or a token for the wrong audience is rejected —
      each case tested separately, since they fail in different code paths.
- [ ] Every dispatched intent produces exactly one audit record, including intents that failed
      upstream. A failed send is the case an audit log exists for.

## Notes

`m365_admin/` already implements the sign-in half of this. Read `m365_admin/auth_state.py` and
`m365_admin/services/token_service.py` before designing anything new — the deferred admin UI in
ADR 0010 partly exists.
