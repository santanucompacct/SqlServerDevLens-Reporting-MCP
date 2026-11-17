# Security boundaries and responsible reporting

## Enforced by the service

- Published objects/columns and restricted operations are allowlisted.
- Filter values are bound SQL parameters.
- Participant authentication and dataset grants are checked for requests.
- OAuth codes, resources, clients, PKCE proofs and registered callbacks are validated.
- Refresh rotation, replay detection, consent scope intersection and revocation are enforced.
- Admin endpoints require a separate admin token; admin mutations check origin when supplied.
- Request, result, row, text, concurrency and SQL timeout bounds limit operations.
- Publication and dictionary saves use revision checks; catalog publication is atomic.
- SQL provider diagnostics are password-redacted in private administrator responses; participant-facing failures are generic.

## Enforced by deployment and SQL

Use a dedicated SQL principal granted SELECT only on approved views. ApplicationIntent=ReadOnly is a connection preference, not a permission boundary. Keep SQL/firewall paths approved, validate SQL TLS and avoid broad database roles.

Keep admin access behind an SSH tunnel or VPN. The supplied systemd service runs as a separate unprivileged identity and binds to loopback. The Caddy template exposes only explicit MCP/OAuth routes.

Different datasets in one instance share its active SQL connection. Use different instances for different database connections or isolation requirements.

## Business rules are instructions

Descriptions, row grain and reporting rules help an AI interpret results; they do not implement row security, field hiding, cancellation checks or accounting reconciliations.

A rule such as “never display internal_hash” is a useful presentation instruction, but a published column can still be requested through query_dataset. Private admin previews also show published fields. To prohibit delivery of a value, omit that field from the reporting catalog or publish a view that does not expose it. The current compiler also requires filter fields to be published, so a hidden filter-only field needs a future feature or a separately scoped SQL view.

Dataset grants allow all rows in the dataset. Built-in company/branch row-level authorization is not implemented. Use distinct approved views/datasets with SQL-enforced scope when participants need different rows.

## Protect persistence and backups

Never commit SQL passwords, tokens, private keys, runtime state manifests, OAuth databases or TLS keys. Keep customer dictionaries and redacted operational evidence private. Synthetic demonstration passwords in disposable CI are not deployment credentials.

Back up state manifests with referenced immutable catalogs, setup.json, the protected password sidecar, protected environment settings, OAuth SQLite state and persistent Caddy TLS data. Use SQLite's backup API or stop the service for a consistent database copy. Protect restore files and backups with the same access controls as the live data.

Do not log administrator/participant tokens, OAuth codes, cookies, client secrets, SQL passwords or customer report rows in routine deployment evidence. Show counts and pass/fail results instead.

## Reporting correctness

Use documented date bounds, units, return signs and row grain. Never sum limited samples into purported full-period totals. A product filter can invalidate bill-level measures stored on only one line; a date filter can hide customer attributes placed on an arbitrary row.

Current balances and aging are not historical as-of balances unless the source explicitly reconstructs them. Reconcile new reports against a trusted ERP/accounting reference and record material limitations.
