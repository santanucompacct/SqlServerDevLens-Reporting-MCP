# Linux deployment template

Provision the service after validating the application in CI and synthetic mode. These instructions are templates for Ubuntu 24.04 with Python 3.12 and Microsoft ODBC Driver 18 installed. Use a separate service identity for each instance.

For a first instance named `example`, install the reviewed wheel into `/opt/devlens-reporting/venv`, create a `devlens-example` system user, and create `/var/lib/devlens-reporting/example` owned by that user with mode 0700. Copy the synthetic starting configuration there as `instance.json` and set file mode 0600. Replace its catalog and connection through the private portal before connecting real users.

Copy `devlens-reporting@.service` into `/etc/systemd/system/`. Create `/etc/devlens-reporting/example.env` from the environment template. Keep that environment file owned by root with mode 0600. Generate an administrator token using `python3 -c 'import secrets; print(secrets.token_hex(32))'` and store it there with the SQL password and the configuration path. Remove placeholder values. Enable `DEVLENS_DEMO=1` only while testing synthetic data.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now devlens-reporting@example
sudo systemctl status devlens-reporting@example
```

On your administrator computer, create an SSH tunnel with your own key and server address.

```bash
ssh -i /path/to/key.pem -L 8101:127.0.0.1:8101 administrator@server.example.com
```

Open `http://127.0.0.1:8101/admin` and authenticate. Keep the terminal tunnel open while administering. For additional instances, assign another port and separate state directory, service user and environment file. Instance provisioning is a Linux administration step in this version.

Use the Caddy template to expose `/mcp` and explicit OAuth discovery/sign-in/token endpoints for a real domain. Point the domain's DNS A record to the server's static public IP, configure the firewall for HTTPS and the chosen certificate-validation method, and set the host allowlist in each service environment. Several domains can share one public IP. Keep database connections and the administration interface on approved private paths.

Before accepting participants, verify SQL permissions with the reporting login, reconcile trusted report totals, test participant access and revocation, and restart each instance independently. Confirm enabled services start after VM boot. Back up the private state and protected secret references using your deployment backup policy. Log retention must be bounded on small disks.


For OAuth, set `DEVLENS_OAUTH_ISSUER=https://your-instance-domain` in that instance's private environment file and restart it. The public issuer must match the actual TLS hostname. The updated Caddy template leaves administration private and exposes only the named MCP/OAuth routes. Dynamic client registration is advertised, so ChatGPT does not require manually copied client credentials. Participants sign in with their own participant token on the DevLens consent page, then review and approve dataset access.

OAuth state is created as mode-0600 `oauth.sqlite3` in the instance state directory by its service account. Include it in protected backups and never share it between issuers or instances. Existing bearer-token clients, participant grants, SQL settings and catalog files remain supported. Do not copy administrator or SQL credentials into an OAuth client configuration.
