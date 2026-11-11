# SQL Server DevLens Reporting MCP

An MIT-licensed, catalog-driven reporting service for SQL Server. Authorized AI clients discover approved datasets, read their business dictionary, and request bounded, parameterized SELECT queries. A private administration webpage manages the connection, published catalog and participant grants.

**Version 0.4.0 is an implementation for pilot evaluation, including OAuth for remote MCP clients.** It uses Python 3.12, FastAPI, the official MCP Python SDK and Microsoft ODBC Driver 18. It is a standalone service alongside [SQL Server DevLens MCP](https://github.com/santanucompacct/SqlServerDevLens-MCP). SQL Server remains responsible for query execution.

## Included in this version

- One independent SQL connection and catalog per process. Run several processes as separate Linux service instances.
- Streamable HTTP MCP at `/mcp` with `list_datasets`, `describe_dataset` and `query_dataset`.
- Participant bearer tokens stored as SHA-256 hashes, plus optional OAuth authorization-code + PKCE, rotating refresh tokens and linked-session revocation. Dataset grants remain the access boundary.
- Private `/admin` webpage for connection setup/testing, table/view discovery, column inspection, JSON dictionary import/export, participant token generation, schema validation and publishing.
- Atomic configuration publication with revision checks. Local browser edits remain drafts until publication succeeds. Drafts are lost when the page closes unless exported.
- Detail rows, filtered results, grouped totals, single metrics, and time series through grouping on an existing date/bucket column. Use views to define month buckets and business calculations.
- Row caps, response-size checks, per-process query concurrency of two, connection/query timeouts and bounded request bodies.
- Synthetic demo, automated API/MCP tests, opt-in live SQL Server tests, Python package CI, Linux service and HTTPS proxy templates.

The initial query API operates on one dataset per call. Relationships are documented metadata. Build reporting views for joins and calculated fields. Clients can make several calls for a summary and supporting detail. Each result reports its catalog version, query filters and truncation state. Separate calls may observe different data. Truncated results require a narrower query or SQL aggregation; result paging is planned.

External identity-provider single sign-on, company/branch row restrictions, an admin portal that provisions processes, and multi-result snapshots are future work. Dataset grants are the access boundary in this release. Publish different scoped views/datasets for participants who require different row access. Host support for bearer tokens, remote MCP and custom apps varies. Verify the intended ChatGPT or Gemini client before a rollout. This release does not claim account-level compatibility testing.

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

MCP `query_dataset` takes this object under the argument `query`. Detail queries use `fields`; aggregate queries use `group_by` and `measures`. Supported measures are sum, avg, min, max and non-null count of a column. Filters combine with AND and support eq, ne, gt, gte, lt, lte, is_null and is_not_null. Ordering is ascending. Monetary SQL decimals are returned as exact strings. Maximum row count is 1000, maximum checked result size is 2 MB, maximum text cell length is 16000 characters, and maximum request size is 256 KiB. Limits protect response handling; query cost also depends on indexes, the source views and data volume. ODBC query timeout defaults to 60 seconds (configurable up to 120); connection timeout is 10 seconds.

## Connect a real SQL Server

Install [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server) on the Linux host. Disable demo mode by removing `DEVLENS_DEMO` from the environment. Configure the SQL hostname, port, database, SQL username and password environment-variable name through `/admin`. Enter the actual password in the separate **SQL password** field and click **Save SQL credentials**, then test the connection. Special characters, spaces and Unicode are supported. Saving immediately replaces this instance's active password. The password is stored in a separate mode-0600 `sql-password` file beside the state manifest and is read on each connection. Include that protected file in deployment backups. It takes precedence over the environment fallback. Existing `DEVLENS_SQL_PASSWORD` configurations continue to work when a saved password file is absent. The advanced password environment field contains a variable name such as `DEVLENS_SQL_PASSWORD`.

SQL TLS settings match the original DevLens defaults. **Encrypt SQL connection** defaults on and **Trust server certificate** defaults off. Both options are available in the admin page and are saved with the connection. Use a hostname matching the SQL certificate and a trusted issuing CA. Selecting Trust server certificate skips server identity verification, so use it for controlled connection testing. Turning encryption off selects ODBC Optional mode, where the SQL server can still require encryption. These settings apply to the connection from the reporting service to SQL Server. Public MCP HTTPS is configured separately by Caddy. `ApplicationIntent=ReadOnly` is a connection preference. Enforce permissions with a dedicated SQL principal granted SELECT only on approved reporting objects. Avoid db_owner, db_datareader and inherited broad grants. Configure and verify that SQL identity separately through your database administration process.

The catalog itself is the object/column allowlist. Each dataset must include a description, row grain, SQL schema/object and described columns. Columns have text, number, date or boolean kinds. Add units, business rules and relationship metadata where relevant. See [examples/instance.json](examples/instance.json). Metadata inspection can seed the dictionary, but business meanings and cancellation, date and accounting rules require administrator review. Publishing verifies each configured projection with a zero-row SELECT. The catalog's declared kinds and business rules require review against SQL metadata and reconciliation against a trusted report.

The server generates SQL from validated identifiers and approved operations. Clients cannot submit SQL text, arbitrary expressions, joins, procedure executions or changes through the tool API.

## Linux instances and HTTPS

Use one configuration file, protected environment file, service identity and loopback port per instance. A shared code installation can serve several independent instances. Each process has its own query limits and SQL identity. Run one worker per instance because the publication lock is process-local.

See [deploy/README.md](deploy/README.md), [systemd template](deploy/devlens-reporting@.service), [environment template](deploy/instance.env.example), and [Caddy template](deploy/Caddyfile.example). The service binds to loopback. Access the administrator page through a VPN or SSH tunnel. Expose `/mcp` and only the explicit OAuth endpoints in the Caddy template through the reverse proxy. Keep `/admin` private.

Several instance domains can point to one static public IP. Each hostname routes to its own loopback service port. Caddy can obtain and renew HTTPS certificates when DNS and certificate-validation connectivity are configured. Configure `DEVLENS_ALLOWED_HOSTS` to include each public hostname, retaining loopback host entries for administration. The server rejects unapproved browser Origin headers on MCP. OAuth-enabled instances allow their own issuer origin and the ChatGPT/Gemini origins.

The process reads its published state manifest on each request. On publication, the data dictionary is saved as a separate immutable `catalog-<sha256>.json` beside that manifest. The manifest points to the current catalog and includes SQL connection metadata and participant token hashes, with secrets referenced through environment variables. Inline catalogs remain supported for initial setup. Back up the manifest together with its referenced catalog. Older catalog files remain available for operational history and can be pruned during maintenance after checking references. Keep state and environment files outside the source checkout and restrict permissions. Participant list edits are published atomically; changes take effect on subsequent requests. An already-running query may finish after revocation.

## Verification

```bash
python -m pytest -q
```

Ordinary tests use synthetic SQLite data while compiling SQL Server queries. CI exercises the real admin and consent webpages in Chrome/Chromium and also provisions a disposable SQL Server using the official Driver 18 libraries from its image, and verifies real SELECT execution, exact decimal serialization and write denial under a restricted login. The integration test verifies rejection of the disposable SQL Server's self-signed certificate under default settings, then tests explicit certificate trust and optional encryption using the actual driver. Connection-test failures are categorized as TLS, login, database access, timeout, network or metadata errors with password-redacted provider details available only in the private administrator connection test. The password used for the failed operation and configured connection identity fields are removed before the diagnostic is returned. To run that integration test locally, set `TEST_SQL_SERVER=localhost,1433` and `TEST_SQL_SA_PASSWORD` for a fresh disposable SQL Server, then run `python -m pytest tests/test_sqlserver.py -q`. The fixture creates its own database and SQL login.

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

SQL operations explicitly consume validation results and close cursors before closing connections, including truncated queries and errors. Reporting connections disable ODBC pooling to avoid reusing handles with pending results.


## Guided SQL setup and field remarks (0.3.0)

Open the private administration page through the instance's SSH tunnel.

1. Click **Load published configuration** with the administrator token. Saved setup settings are loaded automatically, falling back to the published connection on older installations.
2. Enter the SQL host, port, database, login and TLS options. Click **Save SQL details** (or **Update SQL details** after the first save). Save or replace the password separately with **Save SQL credentials**. Passwords are never returned to the page.
3. Click **Test SQL and List object** and select a visible table or view from the dropdown. The help section generates an example object-level SELECT grant for a database administrator to run.
4. Review **Name**, **SQL type**, **Nullable** and editable **Remarks**. Click **Save field remarks**. Selecting the same object again reloads its saved remarks alongside current SQL metadata.

Private setup drafts are stored atomically in `setup.json` alongside the instance state with owner-only file permissions. No additional database service is required. Remarks are scoped to server, port, database, SQL username, schema and object. Schema refresh preserves remarks for existing column names and leaves new columns blank. A revision check rejects conflicting saves from another administrator. The SQL password remains in the separate protected `sql-password` file.

Saving SQL details or field remarks does not change participant datasets or grants. The advanced dataset publishing and participant controls remain available below the setup workflow. A later publish activates the connection and catalog currently entered in those controls. Saving a password continues to replace the active instance password immediately.


## Prepare, publish and test participant access (0.3.0)

After selecting an object with saved field remarks, use the guided **Prepare and publish reporting dataset** section:

1. Enter a dataset ID, business purpose, intended row level and reporting rules (one rule per line).
2. Click **Prepare reporting dataset**. The server checks that saved metadata still matches SQL Server and converts saved remarks into column descriptions. All fields require saved nonblank remarks. Unsupported SQL types and more than 100 columns are rejected. Review and adjust the inferred reporting types.
3. Review the publication impact, acknowledge it and click **Validate and publish dataset**. SQL connectivity and catalog column access are validated before atomic publication. Existing datasets and grants are retained. The initial example Sales dataset can be removed when it is the sole dataset and no participants exist. Publishing activates the current SQL connection for the whole instance.
4. Use **Preview published dataset** for a small read-only sample.
5. In **Participant access and connection**, enter a unique participant name, select published datasets and click **Create participant and activate token**. Copy the token once. Only its SHA-256 hash is stored. Revoke access from the participant table. Both changes take effect immediately and reject stale configuration revisions.
6. **Test participant access** verifies the token and dataset grant and executes a small bounded query using the same authorization and query path as reporting tools. This is an internal service check, not verification of an external client connection. The MCP HTTP protocol is also covered by automated tests.

Dataset grants allow all rows of the chosen dataset. They are not franchise-specific row security. For an external connection, use the displayed MCP endpoint and a participant bearer token with a compatible MCP client. OAuth is available when the instance has an explicit HTTPS issuer configured; see the setup below. Administrator tokens must never be shared with reporting participants.

Preparation leaves the published catalog unchanged until the administrator explicitly publishes. Prepared drafts are held in the page; re-prepare from saved remarks after a refresh. The advanced JSON controls remain available.


## Preview date filters and SQL deadlines (0.3.1)

The guided published-dataset preview uses the first date field (preferring `doc_date`) and the latest seven local calendar days by default. Set From and inclusive To dates before previewing. The generated query uses a lower inclusive bound and an exclusive bound for the following day, which also includes the full To day for datetime columns. Choose No date filter explicitly for an all-history preview. Undated datasets continue to preview without a date filter.

SQL execution has a per-connection `query_timeout_seconds` setting, default 60 seconds and maximum 120. The login timeout stays at 10 seconds. Older saved connections receive the new execution default without losing their field dictionary. Saving SQL details stores a setup draft; publishing activates modified connection settings for reporting.

Private administrator previews now distinguish query timeouts from connectivity problems and may show password-redacted provider diagnostics for otherwise unclassified SQL failures. Participant-facing query failures remain generic. A row limit does not guarantee a fast query when a view must aggregate or join substantial data first; date filters and database-side performance review remain important.


## OAuth connections (0.4.0)

Enable OAuth separately for each hosted instance by setting its canonical public HTTPS origin, without a path:

```dotenv
DEVLENS_OAUTH_ISSUER=https://reports.example.com
DEVLENS_OAUTH_REDIRECT_HOSTS=chatgpt.com,chat.openai.com,oauth-redirect.googleusercontent.com
```

Restart the service and deploy the updated Caddy template. OAuth is disabled when `DEVLENS_OAUTH_ISSUER` is unset. Existing bearer-token clients keep working. Each issuer requires its own state directory, service identity and `oauth.sqlite3`. The database is bound to that issuer; changing the issuer while reusing its OAuth database is rejected. Back up the protected database with the instance state, catalog, setup dictionary and SQL password file. Never commit them to Git.

### Connect ChatGPT

1. Publish a reporting dataset and create a participant with explicit dataset grants through the private administration page. Keep the participant token privately; it is displayed only when created.
2. Add the public `https://reports.example.com/mcp` URL in ChatGPT's custom MCP server dialog. Choose **OAuth** and, if requested, **Dynamic Client Registration (DCR)**. Do not enter the participant token as a client ID or client secret.
3. When redirected to this instance's **DevLens Reporting** consent page, enter the participant token. Do not enter a SQL password, administrator token, ChatGPT password or Google password.
4. Review the application, exact return address, participant and permitted datasets. Choose **Allow read-only reporting access**. ChatGPT receives separate OAuth credentials and never receives the participant token through this flow.
5. Test `list_datasets`, `describe_dataset` and a date-filtered `query_dataset` in the connected account. The protocol tests in this repository do not replace this actual account acceptance check.

This is first-party DevLens participant authentication. It is not Sign in with Google, Microsoft or ChatGPT. Gemini/other clients can use standards-based discovery and DCR with a permitted callback, but product availability and the actual client flow require separate verification.

### Protocol and storage

- Discovery: `/.well-known/oauth-protected-resource/mcp` (also the root alias) and `/.well-known/oauth-authorization-server`.
- OAuth endpoints: `/register`, `/authorize`, `/token`, `/revoke` and `/oauth/consent`. Only these explicit paths and MCP should be proxied publicly.
- Authorization code + mandatory S256 PKCE. Authorization codes last two minutes and cannot be exchanged twice. Responses include RFC 9207 `iss`, and the resource is bound to this instance's `/mcp` URL. A different requested resource is rejected.
- Token endpoint client methods: `none`, `client_secret_post`, `client_secret_basic`. DCR is supported. CIMD and `private_key_jwt` are not advertised in this release.
- Access tokens last up to one hour. Refresh grants last at most 30 days, without sliding extension. Refresh rotation invalidates the previous access token; reuse of a consumed refresh token revokes its entire grant.
- Only SHA-256 hashes of high-entropy access/refresh tokens, codes and client secrets are stored. SQLite is owner-only and survives restarts. Participant tokens remain in the published configuration as hashes only.
- OAuth access is the intersection of datasets consented to and currently granted to the same participant credential. Removing a participant, changing its credential or reducing permissions revokes/shrinks linked grants. Later restoring permissions does not silently expand those grants; reauthorization is required.
- Private **Refresh OAuth connections** and **Revoke OAuth access** controls manage linked grants. Revoking an OAuth connection does not change the participant's separate direct bearer token. Revoking the participant blocks both paths. Already-running queries may finish after revocation.
- The consent flow uses Secure/HttpOnly/SameSite cookies, one-use pending requests, origin/CSRF checks, HTML escaping, frame protection, request caps and bounded registration/login rates. No original participant credential is returned in OAuth responses.
- Callback URIs are registered and matched exactly. Public callbacks must use HTTPS, have an explicitly permitted hostname, and contain no credentials, fragment or reserved OAuth response query fields. Default hostnames are listed above. For a different provider, explicitly approve its callback hostname before connecting. Localhost HTTP callbacks are disabled by default; `DEVLENS_OAUTH_ALLOW_LOOPBACK=1` is an explicit opt-in for trusted local clients.

The implementation reuses the official MCP SDK's authorization, registration and PKCE/token protocol handlers. Production deployments with broader identity requirements can integrate an established identity provider; this pilot does not claim third-party security certification or verified external account identity.
