# Authentication and client connection

## OAuth setup

Set the public origin in each instance's protected environment and restart that service:

```dotenv
DEVLENS_OAUTH_ISSUER=https://reports.example.com
DEVLENS_OAUTH_GRANT_DAYS=30
```

The origin must match the public TLS hostname. Do not include /mcp in the issuer. The protected resource is the issuer plus /mcp. The OAuth database is issuer-bound; do not share it across instances.

Expose the explicit routes in deploy/Caddyfile.example:
- /mcp and /mcp/*
- /.well-known/oauth-authorization-server
- /.well-known/oauth-protected-resource and /.well-known/oauth-protected-resource/mcp
- /authorize, /token, /register, /revoke and /oauth/consent

Keep /admin private.

## Client connection

Publish the approved dataset, create a participant with explicit dataset grants, and give that person their participant token privately.

In the client's remote MCP connection dialog, use https://reports.example.com/mcp and OAuth. Choose dynamic client registration if requested; leave manually supplied client ID/secret unset for DCR. The DevLens HTTPS page asks for the participant token, then shows the application callback and consented datasets.

This is first-party DevLens sign-in. It is not Google, Microsoft or ChatGPT single sign-on. Never use the SQL password or administrator token on the participant consent page. The original participant token is not passed to the connecting application.

Verify list_datasets, describe_dataset and a bounded date-filtered query in the actual client/account. A protocol test alone does not prove actual account connectivity. Other clients, including Gemini, need separate feature/callback verification.

## Lifetimes and revocation

| Item | Policy |
| --- | --- |
| Pending sign-in | Ten minutes |
| Authorization code | Two minutes, single use |
| Access token | Up to one hour |
| Refresh grant default | Absolute 30 days, not sliding |
| Configured finite grants | DEVLENS_OAUTH_GRANT_DAYS=1 through 3650 |
| No fixed expiry | Explicit DEVLENS_OAUTH_GRANT_DAYS=0 |
| Refresh rotation | Old access invalidated; consumed refresh replay revokes the grant |

The no-fixed-expiry policy extends existing unexpired, unrevoked refreshable connections on startup without widening their consented datasets. It does not restore expired/revoked connections. An internal year-9999 timestamp represents no fixed expiry in storage. A finite setting later applies to new grants; revoke existing no-expiry grants explicitly if they must adopt a shorter lifetime.

Normal shutdowns/restarts preserve state when the private files are retained. Client removal, lost refresh credentials, participant changes, replay or revocation can still require reconnection.

Admin controls can revoke an individual OAuth grant or the participant. Participant revocation blocks both direct bearer and OAuth access. Adding datasets does not silently expand a previous OAuth consent. An already-running SQL query may finish after revocation.

## Protocol details

The implementation reuses official MCP SDK authorization, registration and token/PKCE handlers. It supports S256 PKCE, instance resource binding, RFC 9207 issuer responses, DCR, and none/client_secret_post/client_secret_basic token client methods. It does not advertise CIMD or private_key_jwt.

Default callback hosts are chatgpt.com, chat.openai.com and oauth-redirect.googleusercontent.com; public callbacks require HTTPS port 443. Additional exact hostnames can be explicitly configured through DEVLENS_OAUTH_REDIRECT_HOSTS. Local HTTP loopback callbacks are disabled unless DEVLENS_OAUTH_ALLOW_LOOPBACK=1 is deliberately set for a trusted local client.

Only hashes of high-entropy access/refresh tokens, codes and client secrets are persisted. SQLite is mode 0600. Registration and sign-in are capacity/rate limited.

## Browser consent correctness

Each pending request has a separate Secure/HttpOnly/SameSite cookie. Reloading its form and moving from sign-in to review preserve that request's valid CSRF proof. Completing one request does not remove another tab's proof.

The consent CSP allows form navigation to self and the validated callback origin. Exact callback matching remains enforced by the authorization server. This is necessary because Chrome checks form-action on redirects after form submission. Browser regression tests follow a real cross-origin HTTPS redirect, rather than replacing it with a simulated success page.

The form uses same-origin referrer policy so Chrome preserves its same-origin POST Origin. Responses containing authorization redirects use no-referrer. All OAuth responses avoid caching.
