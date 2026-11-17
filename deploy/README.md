# Linux installation and HTTPS

These steps describe a fresh Ubuntu 24.04 x64 installation. Use new paths/instance names; do not overwrite an existing instance's private state during an upgrade.

## 1 Install prerequisites and source

Install Python 3.12, venv support and Git. For real SQL connections also install [Microsoft ODBC Driver 18](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server) and its OS dependencies using Microsoft's instructions for your distribution.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git ca-certificates
sudo mkdir -p /opt/devlens-reporting
sudo git clone https://github.com/santanucompacct/SqlServerDevLens-Reporting-MCP.git /opt/devlens-reporting/source
sudo python3 -m venv /opt/devlens-reporting/venv
sudo /opt/devlens-reporting/venv/bin/python -m pip install /opt/devlens-reporting/source
sudo install -m 0644 /opt/devlens-reporting/source/deploy/devlens-reporting@.service /etc/systemd/system/
```

For production upgrades, use a reviewed immutable source commit and a separate release directory/venv. Preserve state, verify before adoption and retain a rollback link. One process/worker per instance is supported.

## 2 Create an independent instance

The example instance name below is example, with loopback port 8101.

```bash
sudo useradd --system --home-dir /var/lib/devlens-reporting/example --shell /usr/sbin/nologin devlens-example
sudo install -d -m 0700 -o devlens-example -g devlens-example /var/lib/devlens-reporting/example
sudo install -d -m 0700 /etc/devlens-reporting
sudo install -m 0600 -o devlens-example -g devlens-example /opt/devlens-reporting/source/examples/instance.json /var/lib/devlens-reporting/example/instance.json
sudo install -m 0600 /opt/devlens-reporting/source/deploy/instance.env.example /etc/devlens-reporting/example.env
sudoedit /etc/devlens-reporting/example.env
```

Edit the instance field in the new instance.json if desired. The runtime directory/service identity is still selected by the systemd instance name. Each additional instance needs its own user, directory, environment, port and issuer.

Generate a random admin token privately, paste it into the environment file, and retain it in your approved credential store:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Set these values for the new service:

```dotenv
DEVLENS_STATE=/var/lib/devlens-reporting/example/instance.json
DEVLENS_PORT=8101
DEVLENS_ADMIN_TOKEN=REPLACE_WITH_THE_GENERATED_TOKEN
DEVLENS_DEMO=1
DEVLENS_ALLOWED_HOSTS=reports.example.com,reports.example.com:443,127.0.0.1,127.0.0.1:*,localhost,localhost:*
# Optional OAuth after DNS/HTTPS are ready:
# DEVLENS_OAUTH_ISSUER=https://reports.example.com
# DEVLENS_OAUTH_GRANT_DAYS=30
```

Do not use template placeholders as real credentials. Demo mode is for initial synthetic testing. Set DEVLENS_DEMO=0 and restart before testing a real SQL connection.

## 3 Start and administer privately

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now devlens-reporting@example
sudo systemctl status devlens-reporting@example
```

From the operator's computer:

```bash
ssh -i /private/path/reporting-key.pem -L 18101:127.0.0.1:8101 operator@SERVER_IP
```

Keep that session open and visit http://127.0.0.1:18101/admin. Use this instance's administrator token. Follow [the configuration guide](../docs/CONFIGURATION.md) to save SQL settings/password, list objects, save remarks, prepare/publish and create participants.

For the next instance use another service port, for example 8102, and another local tunnel port, for example 18102. An admin token from one instance must not be reused as another instance's token.

## 4 Add public HTTPS

Point your chosen domain's DNS A record to the VM's static public IP. Install [Caddy](https://caddyserver.com/docs/install) and copy/adapt [Caddyfile.example](Caddyfile.example). Provide inbound connectivity for HTTPS and the selected ACME validation method; the common HTTP challenge uses TCP 80 in addition to TCP 443.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

For several hostnames on one IP, add a hostname block for each instance and route it to that instance's loopback port. Expose only the explicit MCP/OAuth paths in the template. Public /admin should return 404; administer through the tunnel.

Caddy obtains and automatically renews its managed TLS certificates while the service and validation paths are available. Preserve its persistent data directory, commonly /var/lib/caddy. A VM that is stopped cannot serve queries or perform renewal checks; verify it after startup. Any extra certificate-expiry guard must be separately configured and tested for your deployment.

## 5 Enable OAuth and verify

Set DEVLENS_OAUTH_ISSUER to the exact HTTPS origin and restart the instance. Keep DEVLENS_ALLOWED_HOSTS consistent with that domain. OAuth-enabled instances allow their own origin and the built-in ChatGPT/Gemini origins for MCP requests.

Verify:
- Public metadata returns 200.
- Anonymous /mcp returns 401 with the protected-resource challenge.
- Public /admin returns 404.
- The SQL identity can SELECT the approved view and cannot write.
- The published dictionary matches the live schema and business report.
- The actual client completes consent, sees only its granted datasets and can query.
- Refresh, revocation, cross-instance rejection and service restart work.

See [OAuth setup](../docs/AUTHENTICATION.md). A service-side test does not replace actual client/account acceptance.

## Backup and operations

Back up the protected environment, instance.json, referenced immutable catalogs, setup.json, sql-password, consistent OAuth SQLite state and Caddy data. Do not commit them. A stopped service or SQLite backup API is needed for a consistent OAuth database snapshot.

Monitor disk/memory, bound logs and retain protected release backups. Restart only the intended instance for its configuration changes. The supplied service runs with a 768 MB memory cap and other systemd restrictions; adjust deliberately for measured workload.

Database, SSH, application and OAuth settings are separate. Configuring a new hostname does not create a database user or publish an approved dataset.
