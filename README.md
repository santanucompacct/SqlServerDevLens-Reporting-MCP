# SQL Server DevLens Reporting MCP

DevLens Reporting is a proposed open-source, read-only reporting service for SQL Server. It lets AI clients explore approved datasets and answer business questions using a versioned data dictionary. It is a separate project from [SQL Server DevLens MCP](https://github.com/santanucompacct/SqlServerDevLens-MCP), which serves broader development and administration workflows.

**Status:** Design stage. This README describes intended behavior and deployment. MIT is the proposed license.

## Instances and administration

Run one independent Linux service instance per SQL connection. Each instance has its own SQL identity, database, object allowlist, catalog, access policy, and MCP endpoint. Multiple instances can share a Linux host and run as separate systemd services under separate identities. A reverse proxy routes each HTTPS hostname to its instance and can manage Let's Encrypt certificates and renewal.

A private web portal will let administrators:

1. Create a named instance and configure its SQL Server hostname, port, database, and authentication settings.
2. Test the connection using the instance's reporting identity and select fully qualified tables or views. Prefer curated views that expose only the fields needed for reporting.
3. Generate a draft JSON catalog from SQL metadata and edit field descriptions, relationships, business rules, and measures.
4. Assign users to datasets and, where applicable, company or branch scopes.
5. Validate the draft, preview a bounded sample, and publish a version. The previous published version continues serving until publication succeeds.

The admin portal is intended for private access through a VPN or SSH tunnel. The public interface is an authenticated Streamable HTTP endpoint, for example `https://reporting.example.com/mcp`.

## Dataset catalog

Each instance has a separate JSON catalog. A dataset entry should define its source object, business purpose, row grain, keys, columns and types, nullability, units, allowed filters and groupings, and approved relationships with join keys and cardinality. Measures should document calculations, date semantics, and rules such as excluding cancelled transactions. The server should validate the catalog against the allowlist and live schema before publication.

## Queries and multiple response shapes

The initial MCP tools are planned as `list_datasets`, `describe_dataset`, and `query_dataset`. Clients request approved fields, filters, groupings, and measures. The server validates those choices and generates parameterized SQL. The first release targets structured queries against published datasets.

| Response shape | Example |
|---|---|
| Detail rows | Show the invoices behind an outstanding balance. |
| Grouped totals | Summarize outstanding amounts by customer or ageing bucket. |
| Single metric | Return total outstanding as of a date. |
| Time series | Compare monthly sales for a selected period. |
| Multiple related results | Return a total, a customer breakdown, and a trend as separately named result sections. |

Use structured JSON as the common response contract. Include dataset and catalog version, applied filters, reporting period or as-of date, and truncation or paging information where applicable. A client can use the same results to answer in prose, render a table, or build a chart. Multiple related sections and paged results are design targets. If sections are calculated separately, the response should state the applicable consistency or snapshot semantics.

## Access and read-only controls

Use a dedicated SQL principal per instance with SELECT grants limited to approved objects. Enforce dataset, field, and company or branch scope server-side on each query and result-page request. Use individual OAuth identities for compatible interactive clients, with expiring, revocable tokens for controlled API clients where supported. Each authorized participant can connect to the same instance endpoint. ChatGPT and Gemini onboarding will be verified separately for the intended accounts and client surfaces.

Apply query timeouts, concurrency limits, result caps, and appropriate date limits. Scope cached or paged results to their originating instance and authorized user. Keep secrets outside Git and audit queries without recording sensitive row values by default.

## Pilot and repository scope

Start with two independently configured instances and one approved dataset each. Verify figures against known reports, confirm that differently scoped users see the correct data, check that result limits apply, and restart one instance while the other remains available. This public repository will contain source code, sample catalogs, synthetic SQL fixtures, automated tests, and generic Linux deployment guides. Customer configurations and pilot evidence are tracked separately.
