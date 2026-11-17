# SQL Server DevLens Reporting MCP

An MIT-licensed, read-only reporting service for SQL Server and Azure SQL Database. AI clients discover approved datasets, read their business definitions, and request bounded, parameterized SELECT queries. A private admin webpage manages SQL settings, field descriptions, publication and participant access.

**Current application version: 0.4.1.** This is a working pilot implementation, separate from the development-oriented [SQL Server DevLens MCP](https://github.com/santanucompacct/SqlServerDevLens-MCP). It does not host an AI model: the connected AI client interprets questions, while SQL Server executes the approved reporting queries.

## What is implemented

| Capability | Current behavior |
| --- | --- |
| Multiple instances | One process, SQL connection, catalog, service account, state directory, admin token and port per instance |
| MCP transport | Streamable HTTP at /mcp |
| Reporting tools | list_datasets, describe_dataset, query_dataset |
| SQL access | Dedicated restricted SQL principal; approved objects and columns only |
| Admin workflow | Save SQL settings/password, test/list objects, edit field remarks, prepare/publish, preview, manage participants |
| Metadata | Purpose, row grain, column descriptions, units, business rules and documented relationships |
| Authentication | Participant bearer tokens; optional OAuth authorization code with S256 PKCE and DCR |
| OAuth sessions | One-hour access tokens, rotating refresh tokens, replay detection and revocation |
| Lifetime policy | Default 30 days; configurable finite lifetime or explicit no-fixed-expiry policy |
| Persistence | Revision-checked JSON setup/publication; private SQLite OAuth state; private SQL password sidecar |
| HTTPS | Caddy templates; multiple hostnames can share one static public IP |
| Bulk remarks | Reviewed JSON import through the private admin API, dry run by default |
| Verification | API/MCP tests, actual browser consent redirects and disposable SQL Server integration |

The service has been exercised with real deployments and connected ChatGPT accounts. That experience is not a guarantee of every client, subscription, region or future UI. Test the actual intended account. Gemini remote-MCP/OAuth availability and callbacks require separate client acceptance.

## Documentation

- [Installation, Linux services and HTTPS](deploy/README.md)
- [Configuration and reporting workflow](docs/CONFIGURATION.md)
- [Architecture, files and query contract](docs/ARCHITECTURE.md)
- [OAuth and client onboarding](docs/AUTHENTICATION.md)
- [Security boundaries and privacy](docs/SECURITY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Development history and validation](docs/DEVELOPMENT.md)
- [Changelog](CHANGELOG.md)

## Quick start with synthetic data

Python 3.12 or later is required. The demo does not need SQL Server or an ODBC driver.

```bash
git clone https://github.com/santanucompacct/SqlServerDevLens-Reporting-MCP.git
cd SqlServerDevLens-Reporting-MCP
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
mkdir -p state
cp examples/instance.json state/instance.json
export DEVLENS_STATE="$PWD/state/instance.json"
export DEVLENS_DEMO=1
export DEVLENS_ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
# Display only in your private terminal; do not paste into a chat or public log.
printf '%s\n' "$DEVLENS_ADMIN_TOKEN"
devlens-reporting
```

Open http://127.0.0.1:8101/admin, enter the admin token and load the configuration. The example contains three synthetic sales rows and no participants. Use the admin page to create a participant granted the sales dataset; copy the generated participant token once.

For a real SQL connection, install Microsoft ODBC Driver 18, set **DEVLENS_DEMO=0** in the service environment and restart that instance. Saving SQL settings or clicking Test SQL does not change the runtime demo flag.

## The end-to-end workflow

1. Provision a separate instance and private administration path.
2. Create a restricted SQL principal for approved reporting views.
3. Save SQL connection details and password separately.
4. Test SQL, select the real object, describe every field and save remarks.
5. Define the dataset ID, purpose, row level and reporting rules.
6. Prepare, review, validate and explicitly publish the dataset.
7. Preview a small date-filtered sample and reconcile against a trusted report.
8. Create participants with explicit dataset grants.
9. Connect the AI client through OAuth or a supported bearer-token setup.
10. Verify actual account queries, revocation, isolation and restart behavior.

Saving drafts does not publish. Publishing activates the selected SQL connection for the whole instance. Saving a password replaces the active instance password immediately. Configure a different instance for a different SQL connection.

## Example reporting request

MCP query_dataset receives this object under its query argument:

```json
{
  "dataset": "sales",
  "group_by": ["customer"],
  "measures": [{"column": "amount", "function": "sum", "alias": "total"}],
  "filters": [
    {"column": "doc_date", "op": "gte", "value": "2026-01-01"},
    {"column": "doc_date", "op": "lt", "value": "2026-02-01"}
  ],
  "order_by": ["customer"],
  "limit": 100
}
```

Use fields for detail, or group_by and measures for totals. Measures support sum, avg, min, max and non-null count of a column. Filters combine with AND. Ordering is ascending. Maximum rows: 1000; checked result size: 2 MB; request size: 256 KiB. Decimal amounts are serialized as exact strings.

The API does not accept SQL text, arbitrary expressions, joins or stored procedure execution. Use reporting views for joins, month buckets and calculations. Relationships are explanatory metadata, not executable joins. SQL execution defaults to 60 seconds, configurable up to 120; login timeout is 10 seconds. A small row limit does not guarantee that a complex source view is cheap.

## Important boundaries

- Dataset grants allow all rows of the selected dataset; built-in company/branch row-level authorization is not implemented.
- Business rules guide the AI; they are not executable constraints or an accounting reconciliation engine.
- An internal-only label or prompt does not remove a published column from tool responses. For a strict boundary, omit it from the public-facing catalog or publish an appropriately scoped view. Filter-only hidden columns are not implemented.
- Truncation is reported, but result paging, descending ordering, distinct counts, multi-dataset joins and cross-call snapshots are not implemented.
- Public exports, reports and user prompts must not contain passwords, tokens or private operational identifiers.
- The admin portal configures an existing service instance; it does not provision new Linux processes or databases.

## Public source and private operations

The complete reusable service is in this repository: Python source, private admin UI, query compiler, OAuth provider, models, persistence, synthetic examples, generic templates, bulk-import helper and tests.

Customer domains, server inventory, database names, SQL identities, dictionaries, participant names and redacted deployment evidence belong in a separate private operations repository. **Passwords, private keys, bearer/refresh tokens, OAuth client secrets and TLS private keys belong in neither repository.**

Public contributions must use synthetic examples. See [LICENSE](LICENSE).
