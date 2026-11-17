# Development workflow and validation

## How the product was developed

1. Separate reporting from the development-oriented DevLens server: read-only generated queries, approved datasets and private administration.
2. Build strict models, an allowlisted SELECT compiler and synthetic MCP/API tests.
3. Add independent Linux service instances and hostname routing through one HTTPS proxy.
4. Verify actual ODBC behavior, TLS settings, SQL permissions and connection/metadata errors.
5. Separate saved SQL settings/passwords, field remarks and published catalogs.
6. Add object selection, field grids, guided dataset preparation/publication and date-filtered previews.
7. Add participant grants and verify revocation and cross-instance isolation.
8. Add SDK-backed OAuth discovery, DCR, S256 PKCE, consent, rotating refresh tokens and persistent sessions.
9. Test browser behavior, including parallel sign-ins, reloads and a real cross-origin callback redirect.
10. Add explicit long-lived management-account policy and bulk metadata import.
11. Reconcile real business datasets privately; promote only generic fixes, tools and synthetic tests to public source.

Public source holds reusable product code. Private operations records hold deployment pins, customer dictionaries, runbooks and redacted test evidence. Neither repository is a credential store.

## Run checks

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]' build
python -m pytest -q
python -m build
```

The ordinary run skips opt-in browser and SQL integration tests. CI runs those separately:
- Browser: install Playwright and Chrome/Chromium; set DEVLENS_BROWSER_TEST=1.
- SQL: use a fresh disposable SQL Server and Microsoft ODBC Driver 18; set TEST_SQL_SERVER and TEST_SQL_SA_PASSWORD.

The SQL fixture creates a test database/principal and checks actual SELECT, TLS behavior, decimal serialization and write denial. Never point it at an unreviewed production database.

OAuth regressions cover DCR methods, resource/callback binding, S256 PKCE, expiry, code replay, refresh reuse, grant/participant revocation, issuer isolation, restarts, no silent scope expansion and configurable/no-fixed-expiry lifetimes.

The browser test seeds an isolated local callback fixture to exercise real HTTPS cross-origin navigation without contacting a real user account. Production callback registration restrictions remain unchanged. Do not replace that redirect with a synthetic HTTP 200 response: doing so previously hid a browser CSP failure.

The bulk-import tests check dry-run behavior, private backups, complete-schema matching, one save, read-back verification and rejection of non-loopback admin origins.

## Release acceptance

A green package build is necessary but not sufficient. For a hosted release, record:
- Exact tested/deployed source commit and package version.
- Service identities, loopback ports and public routing.
- SQL identity/object permissions and approved data dictionary.
- Date-filtered reference report reconciliation.
- Actual intended client/account connection.
- Token refresh, revocation, isolation and restart behavior.
- Protected backup/restore procedures.

Deploy by a reviewed immutable source commit, preserve private state, verify health before adoption, and retain a rollback path. Documentation-only changes do not imply that a new runtime build was deployed.

## Scope and next work

Future work includes stronger field-level presentation restrictions, row-scope policies, paging, richer sorting/aggregation, external identity-provider SSO, multi-result consistency and process provisioning. Keep these labeled as planned rather than claiming them as current features.
