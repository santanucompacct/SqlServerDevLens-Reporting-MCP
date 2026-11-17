# Troubleshooting

| Symptom | Check and action |
| --- | --- |
| SQL test shows Sales (demo) | Set DEVLENS_DEMO=0 in that instance's environment and restart it. Saving SQL settings does not change demo mode. |
| password_env pattern mismatch | Enter a variable name there, not a password. Save the password in the separate SQL credentials field. |
| Connection opened but no objects | Check the selected database, object-level SELECT grants and metadata visibility under the reporting identity. |
| Connection busy with results | Upgrade past the cursor-cleanup fix. The service consumes results, closes cursors and disables ODBC pooling. |
| DEFAULT_DATABASE unsupported | Check the platform. Azure SQL Database can use a contained user created directly in the target database. |
| SQL login fails for a contained user | Set the database explicitly, and verify password, username, firewall/network and TLS. |
| Generic participant query failure | Use the private administrator preview for redacted diagnostics. Do not expose raw provider errors to participants. |
| Query timeout | Add an appropriate date filter and inspect source view/index performance. A TOP row limit does not prevent an expensive join/aggregation. |
| Remarks disappear | Save remarks before closing, select the same SQL identity/schema/object, and reload. Check for schema drift. |
| Purpose/rules empty | Saved field remarks and published dataset metadata are different steps. Prepare and explicitly publish, then reload the published object. |
| Configuration changed / HTTP 409 | Another save/publication changed the revision. Reload and reconcile; do not blindly overwrite it. |
| 401 at public /mcp | Expected without a valid participant/OAuth token. Check the intended instance and grant if authenticated. |
| 404 at public /admin | Expected with the supplied proxy. Use the private tunnel. |
| Consent verification failed | Close consumed/expired pages and start a fresh connection from the client. Diagnostic categories identify request expiry, missing cookies or proof mismatch. |
| First Allow click does nothing; second says used | Check that the deployed consent CSP permits its validated callback origin. Do not repeatedly submit the consumed request. |
| OAuth form rejected for null Origin | Check that an old/global no-referrer header is not overriding the consent form's same-origin policy. |
| Old token rejected after refresh | Expected: rotation invalidates the previous access token. Reusing an old refresh token revokes the whole grant. |
| New datasets not available in an existing OAuth connection | New participant permissions do not expand old consent. Reconnect to consent to the additional access. |
| VM restarted but client asks to reconnect | Verify persistent OAuth state and issuer identity. A client can also lose/revoke credentials independently. |
| HTTPS certificate issuance fails | Check public DNS, inbound connectivity and ACME validation. Preserve Caddy's persistent data directory. |
| Bulk import fails | Check complete column-name matching and nonblank remarks. The helper is dry-run by default and refuses public admin URLs/redirects. |

Useful private commands:

```bash
sudo systemctl status devlens-reporting@example caddy
sudo journalctl -u devlens-reporting@example -n 50 --no-pager
sudo systemctl restart devlens-reporting@example
```

Inspect logs privately before sharing; redact credentials and customer data. An uncertain write may already have succeeded. Read the current configuration instead of automatically retrying.
