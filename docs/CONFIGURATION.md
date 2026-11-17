# Configuration and reporting workflow

## Three independent credentials

| Credential | Purpose | Where it belongs |
| --- | --- | --- |
| SQL password | Reporting service connects to SQL | Separate protected sql-password file or environment fallback |
| Administrator token | Private admin webpage/API | Protected per-instance environment; private operator terminal |
| Participant token | Reporting access and first-party OAuth sign-in | Given privately to its participant; server retains only a hash |

A participant token is not a SQL password or OAuth client secret. Each instance needs separate credentials.

## SQL Server versus Azure SQL Database

Use an administrator session to configure the reporting principal. Do not execute security DDL through the reporting MCP. The following are templates; replace names and password privately.

For SQL Server on a VM, create a login in master and a mapped user in the target database:

```sql
USE [master];
GO
CREATE LOGIN [reporting_reader]
WITH PASSWORD = N'REPLACE_WITH_YOUR_OWN_STRONG_PASSWORD',
     DEFAULT_DATABASE = [ReportingDatabase],
     CHECK_POLICY = ON,
     CHECK_EXPIRATION = OFF;
GO
USE [ReportingDatabase];
GO
CREATE USER [reporting_reader] FOR LOGIN [reporting_reader];
GRANT SELECT ON OBJECT::[dbo].[ReportSales] TO [reporting_reader];
GO
```

For Azure SQL Database, connect directly to the target database as an administrator and create a contained user:

```sql
CREATE USER [reporting_reader]
WITH PASSWORD = N'REPLACE_WITH_YOUR_OWN_STRONG_PASSWORD',
     DEFAULT_SCHEMA = [dbo];
GO
GRANT SELECT ON OBJECT::[dbo].[ReportSales] TO [reporting_reader];
GO
```

For a contained user, the connection must explicitly name the target database. Do not use the VM login template's DEFAULT_DATABASE option. Grant only the required objects; avoid db_owner, db_datareader and writer roles. Additional base-object permissions may be needed when ownership chaining or view dependencies differ; have a database administrator inspect that case rather than granting broad access.

## Save and inspect

1. Open /admin through the instance's SSH tunnel.
2. Enter that instance's admin token and load the published configuration.
3. Save SQL host, port, database, username, encryption, certificate trust and query deadline.
4. Save the actual password using Save SQL credentials.
5. Click Test SQL and List Objects; select the approved real view.
6. Review name, SQL type, nullability and each field's remarks; save the remarks.

The password_env field is an environment variable name such as DEVLENS_SQL_PASSWORD, never the actual password. Saved password bytes take precedence over that environment fallback. Passwords are not returned to the webpage. Changing the saved password immediately affects future SQL connections.

SQL encryption defaults on and trust_server_certificate defaults off. Public HTTPS and SQL TLS are separate connections. Use a trusted SQL certificate and matching hostname; certificate-trust bypass is an explicit administrator setting.

## Demo mode is a process setting

DEVLENS_DEMO=1 supplies synthetic SQLite objects even if real SQL settings have been entered. Set DEVLENS_DEMO=0, restart only that instance, and retry object discovery before publishing. The synthetic Sales object is not evidence that a real SQL login was verified.

## Metadata and publication

Field remarks are saved in setup.json, scoped to SQL host, port, database, username, schema and object. This is the reporting service's dictionary; it does not add SQL extended properties.

Give each dataset:
- A stable identifier.
- A purpose describing the questions it can answer.
- A row grain explaining what one row represents.
- Nonblank descriptions for every field.
- Units, date definitions, calculation rules and limitations.
- Relationship metadata when useful.

Separate line-level measures from bill/customer-level values. Document returns, sign conventions, cancellation handling, current versus historical balances, mixed units and any values present on only one selected row. Identify internal fields and decide whether they should be omitted from the published catalog.

Prepare the dataset from saved remarks. Preparation checks the live schema and supported types, but does not publish. Review inferred kinds. Publishing validates approved projections with zero-row SELECTs and atomically updates the manifest/catalog using the current revision. The guided UI can remove the initial synthetic dataset when it is the sole dataset and there are no participants.

Existing datasets and participant grants are retained by ordinary guided publication. A new SQL connection affects the whole instance; it must not silently replace a different customer's connection.

## Bulk import without copy/paste

Use [scripts/import_column_remarks.py](../scripts/import_column_remarks.py) from the repository checkout. It uses the standard library, talks only to a loopback admin origin, and refuses redirects so the admin token is not forwarded elsewhere.

The input is JSON, either a name-to-description object or a columns list:

```json
{"columns":[
  {"name":"customer","remarks":"Synthetic customer label."},
  {"name":"doc_date","remarks":"Transaction date for reporting filters."},
  {"name":"amount","remarks":"Synthetic transaction amount."},
  {"name":"branch","remarks":"Synthetic branch label."}
]}
```

For Word/Excel source dictionaries, first extract and review an equivalent JSON file. Preserve meanings; do not invent missing descriptions. Customer dictionaries must remain private.

In a private Bash terminal, load the admin token without saving it in shell history:

```bash
read -r -s -p "Administrator token: " DEVLENS_ADMIN_TOKEN
export DEVLENS_ADMIN_TOKEN
printf '\n'
python scripts/import_column_remarks.py --admin-url http://127.0.0.1:18101 --input reviewed-remarks.json --schema dbo --object ReportSales
# After reviewing the dry-run result:
python scripts/import_column_remarks.py --admin-url http://127.0.0.1:18101 --input reviewed-remarks.json --schema dbo --object ReportSales --apply
unset DEVLENS_ADMIN_TOKEN
```

Input names must match the complete live object, ignoring case; blank/duplicate/missing/extra fields fail before saving. Apply saves once against the current revision, creates a mode-0600 local pre-change backup, and reads every remark back. SQL types/nullability remain those returned by the live server. The helper does not change SQL permissions, publish a dataset or alter participant grants. On an uncertain HTTP failure, inspect server state before retrying. Keep .devlens-import-backups private and out of Git.

## Preview and account acceptance

Use a small date-filtered preview. Dated datasets default to the last seven browser-local calendar days; To is inclusive and converted into the next day's exclusive bound. Choose an explicit date range appropriate to the report.

Create a participant with selected datasets, copy its token once, and test through the intended AI account. Reconcile server-side totals against a trusted ERP report before relying on a new metric. Do not total a truncated sample. Use a separate reporting view when a metric or access boundary cannot be expressed correctly.
