# SQL Server DevLens Reporting MCP

An MIT-licensed, catalog-driven reporting service for SQL Server. Authorized AI clients discover approved datasets, read their business dictionary, and request bounded, parameterized SELECT queries. A private administration webpage manages the connection, published catalog and participant grants.

**Version 0.1.3 is an initial implementation for pilot evaluation.** It uses Python 3.12, FastAPI, the official MCP Python SDK and Microsoft ODBC Driver 18. It is a standalone service alongside [SQL Server DevLens MCP](https://github.com/santanucompacct/SqlServerDevLens-MCP). SQL Server remains responsible for query execution.

## Included in this version

- One independent SQL connection and catalog per process. Run several processes as separate Linux service instances.
- Streamable HTTP MCP at `/mcp` with `list_datasets`, `describe_dataset` and `query_dataset`.
- Individual bearer tokens stored as SHA-256 hashes, with explicit dataset grants and revocation on the next request.
- Private `/admin` webpage for connection setup/testing, table/view discovery, column inspection, JSON dictionary import/export, participant token generation, schema validation and publishing.
- Atomic configuration publication with revision checks. Local browser edits remain drafts until publication succeeds. Drafts are lost when the page closes unless exported.
- Detail rows, filtered results, grouped totals, single metrics, and time series through grouping on an existing date/bucket column. Use views to define month buckets and business calculations.
- Row caps, response-size checks, per-process query concurrency of two, connection/query timeouts and bounded request bodies.
- Synthetic demo, automated API/MCP tests, opt-in live SQL Server tests, Python package CI, Linux service and HTTPS proxy templates.

The initial query API operates on one dataset per call. Relationships are documented metadata. Build reporting views for joins and calculated fields. Clients can make several calls for a summary and supporting detail. Each result reports its catalog version, query filters and truncation state. Separate calls may observe different data. Truncated results require a narrower query or SQL aggregation; result paging is planned.

OAuth, delegated identity, company/branch row restrictions, an admin portal that provisions processes, and multi-result snapshots are future work. Dataset grants are the access boundary in this release. Publish different scoped views/datasets for participants who require different row access. Host support for bearer tokens, remote MCP and custom apps varies. Verify the intended ChatGPT or Gemini client before a rollout. This release does not claim account-level compatibility testing.

## Try locally with synthetic data

No SQL Server is needed for the synthetic demo.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
mkdir -p state
cp examples/instance.json state/instance.json
export DEVLENS_STATE="$PWD/state/instance.json"
export DEVLENS_DEMO=1
export DEVLENS_ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
# Display this locally so you can paste it into the private admin page.
printf '%s\n' "$DEVLENS_ADMIN_TOKEN"
devlens-reporting
```

Open `http://127.0.0.1:8101/admin`, enter the administrator token and load the configuration. The demo contains three synthetic sales and starts with no participants. Generate a participant token, add `sales` to that participant's dataset list, and publish. Copy the token once and give it to your MCP client through its supported Authorization header configuration. Keep it private.

The admin preview accepts this query. All examples use synthetic names and figures.

```json
{
  "dataset": "sales",
  "group_by": ["customer"],
  "measures": [{"column": "amount", "function": "sum", "alias": "total"}],
  "filters": [{"column": "doc_date", "op": "gte", "value": "2026-01-01"}],
  "order_by": ["customer"],
  "limit": 100
}
```

MCP `query_dataset` takes this object under the argument `query`. Detail queries use `fields`; aggregate queries use `group_by` and `measures`. Supported measures are sum, avg, min, max and non-null count of a column. Filters combine with AND and support eq, ne, gt, gte, lt, lte, is_null and is_not_null. Ordering is ascending. Monetary SQL decimals are returned as exact strings. Maximum row count is 1000, maximum checked result size is 2 MB, maximum text cell length is 16000 characters, and maximum request size is 256 KiB. Limits protect response handling; query cost also depends on indexes, the source views and data volume. ODBC query timeout is 20 seconds and connection timeout is 10 seconds.

## Connect a real SQL Server

Install [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server) on the Linux host. Disable demo mode by removing `DEVLENS_DEMO` from the environment. Configure the SQL hostname, port, database, SQL username and password environment-variable name through `/admin`. Enter the actual password in the separate **SQL password** field and click **Save SQL password**, then test the connection. Special characters, spaces and Unicode are supported. Saving immediately replaces this instance's active password. The password is stored in a separate mode-0600 `sql-password` file beside the state manifest and is read on each connection. Include that protected file in deployment backups. It takes precedence over the environment fallback. Existing `DEVLENS_SQL_PASSWORD` configurations continue to work when a saved password file is absent. The advanced password environment field contains a variable name such as `DEVLENS_SQL_PASSWORD`.

SQL TLS settings match the original DevLens defaults. **Encrypt SQL connection** defaults on and **Trust server certificate** defaults off. Both options are available in the admin page and are saved with the connection. Use a hostname matching the SQL certificate and a trusted issuing CA. Selecting Trust server certificate skips server identity verification, so use it for controlled connection testing. Turning encryption off selects ODBC Optional mode, where the SQL server can still require encryption. These settings apply to the connection from the reporting service to SQL Server. Public MCP HTTPS is configured separately by Caddy. `ApplicationIntent=ReadOnly` is a connection preference. Enforce permissions with a dedicated SQL principal granted SELECT only on approved reporting objects. Avoid db_owner, db_datareader and inherited broad grants. Configure and verify that SQL identity separately through your database administration process.

The catalog itself is the object/column allowlist. Each dataset must include a description, row grain, SQL schema/object and described columns. Columns have text, number, date or boolean kinds. Add units, business rules and relationship metadata where relevant. See [examples/instance.json](examples/instance.json). Metadata inspection can seed the dictionary, but business meanings and cancellation, date and accounting rules require administrator review. Publishing verifies each configured projection with a zero-row SELECT. The catalog's declared kinds and business rules require review against SQL metadata and reconciliation against a trusted report.

The server generates SQL from validated identifiers and approved operations. Clients cannot submit SQL text, arbitrary expressions, joins, procedure executions or changes through the tool API.

## Linux instances and HTTPS

Use one configuration file, protected environment file, service identity and loopback port per instance. A shared code installation can serve several independent instances. Each process has its own query limits and SQL identity. Run one worker per instance because the publication lock is process-local.

See [deploy/README.md](deploy/README.md), [systemd template](deploy/devlens-reporting@.service), [environment template](deploy/instance.env.example), and [Caddy template](deploy/Caddyfile.example). The service binds to loopback. Access the administrator page through a VPN or SSH tunnel. Expose only `/mcp` through the reverse proxy.

Several instance domains can point to one static public IP. Each hostname routes to its own loopback service port. Caddy can obtain and renew HTTPS certificates when DNS and certificate-validation connectivity are configured. Configure `DEVLENS_ALLOWED_HOSTS` to include each public hostname, retaining loopback host entries for administration. The server rejects browser Origin headers on MCP by default.

The process reads its published state manifest on each request. On publication, the data dictionary is saved as a separate immutable `catalog-<sha256>.json` beside that manifest. The manifest points to the current catalog and includes SQL connection metadata and participant token hashes, with secrets referenced through environment variables. Inline catalogs remain supported for initial setup. Back up the manifest together with its referenced catalog. Older catalog files remain available for operational history and can be pruned during maintenance after checking references. Keep state and environment files outside the source checkout and restrict permissions. Participant list edits are published atomically; changes take effect on subsequent requests. An already-running query may finish after revocation.

## Verification

```bash
python -m pytest -q
```

Ordinary tests use synthetic SQLite data while compiling SQL Server queries. CI exercises the real admin webpage in Chromium and also provisions a disposable SQL Server, installs Driver 18, and verifies real SELECT execution, exact decimal serialization and write denial under a restricted login. The integration test verifies rejection of the disposable SQL Server's self-signed certificate under default settings, then tests explicit certificate trust and optional encryption using the actual driver. Connection-test failures are categorized as TLS, login, database access, timeout, network or metadata errors without returning raw provider messages or credentials. To run that integration test locally, set `TEST_SQL_SERVER=localhost,1433` and `TEST_SQL_SA_PASSWORD` for a fresh disposable SQL Server, then run `python -m pytest tests/test_sqlserver.py -q`. The fixture creates its own database and SQL login.

CI builds wheel and source archives as downloadable workflow artifacts. These builds are pilot packages. Customer database reconciliation and real ChatGPT/Gemini onboarding remain deployment acceptance checks.

## Source layout

| Path | Purpose |
|---|---|
| `src/devlens_reporting/` | Service, models, query compiler, storage and admin webpage |
| `examples/` | Synthetic starting configuration |
| `tests/` | API, authorization, MCP, publication and SQL regressions |
| `deploy/` | Generic Linux and HTTPS templates |
| `.github/workflows/` | Test and package automation |

Use synthetic examples in public issues and contributions. Customer catalogs, instance settings and test evidence belong in private deployment records. See [LICENSE](LICENSE).
