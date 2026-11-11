"""MCP OAuth authorization-code/PKCE provider backed by private SQLite state.

Protocol validation uses the official MCP SDK handlers. Participant tokens are
used only on this instance's consent page; clients receive separate credentials.
"""
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .models import Participant

SCOPE = "reporting:read"
ACCESS_SECONDS = 3600
GRANT_SECONDS = 30 * 86400
COOKIE = "__Host-devlens-oauth"
DEFAULT_REDIRECT_HOSTS = {"chatgpt.com", "chat.openai.com", "oauth-redirect.googleusercontent.com"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def oauth_error(error: str, description: str, status=400):
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


class SQLiteTransaction:
    """Close connections without mutating frozen SDK protocol exceptions."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        return self.connection

    def __exit__(self, kind, value, traceback):
        try:
            return self.connection.__exit__(kind, value, traceback)
        finally:
            self.connection.close()


class OAuthProvider:
    def __init__(self, store, issuer: str, redirect_hosts=None, allow_loopback=False):
        parsed = urlsplit(issuer)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("DEVLENS_OAUTH_ISSUER must be a canonical HTTPS origin")
        self.issuer = issuer.rstrip("/")
        self.resource = self.issuer + "/mcp"
        self.store = store
        self.path = store.path.parent / "oauth.sqlite3"
        self.redirect_hosts = set(redirect_hosts if redirect_hosts is not None else DEFAULT_REDIRECT_HOSTS)
        self.allow_loopback = allow_loopback
        self.rates = {}
        # Precreate with private permissions. No raw participant/access/refresh
        # tokens or OAuth client secrets are written to the database.
        import os
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, secret_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS pending (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, params TEXT NOT NULL,
                    expires REAL NOT NULL, csrf_hash TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    participant TEXT, fingerprint TEXT, datasets TEXT
                );
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, participant TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, datasets TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, grant_id TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(grant_id) REFERENCES grants(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS tokens_grant ON tokens(grant_id);
            """)
            stored = db.execute("SELECT value FROM settings WHERE key='issuer'").fetchone()
            if stored and stored["value"] != self.issuer:
                raise ValueError("OAuth database belongs to another issuer; review the instance configuration")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('issuer',?)", (self.issuer,))

    def db(self):
        return SQLiteTransaction(self.path)

    def rate_allowed(self, operation, limit, request=None):
        now = time.monotonic()
        address = request.client.host if request and request.client else "unknown"
        # The application listens only on loopback; Caddy appends the actual peer
        # as the last forwarded address. Never trust arbitrary remote XFF input.
        if request and address in {"127.0.0.1", "::1"}:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            try:
                address = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        key = (operation, address)
        if key not in self.rates and len(self.rates) >= 4096:
            self.rates = {key: entry for key, entry in self.rates.items() if now - entry[0] < 60}
            if len(self.rates) >= 4096:
                return False
        start, count = self.rates.get(key, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        self.rates[key] = (start, count + 1)
        return count < limit

    def valid_redirect(self, uri):
        value = str(uri)
        parsed = urlsplit(value)
        if len(value) > 2048 or parsed.username or parsed.password or parsed.fragment:
            return False
        if set(parse_qs(parsed.query)) & {"code", "state", "iss", "error", "error_description"}:
            return False
        if self.allow_loopback and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            port = parsed.port
        except ValueError:
            return False
        return parsed.scheme == "https" and parsed.hostname in self.redirect_hosts and port in {None, 443}

    async def get_client(self, client_id):
        with self.db() as db:
            row = db.execute("SELECT metadata FROM clients WHERE id=?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["metadata"]) if row else None

    async def register_client(self, client_info):
        if (not client_info.redirect_uris or len(client_info.redirect_uris) > 10
                or not all(self.valid_redirect(uri) for uri in client_info.redirect_uris)):
            raise RegistrationError("invalid_redirect_uri", "Use a permitted HTTPS OAuth callback URI")
        if (client_info.token_endpoint_auth_method not in {"none", "client_secret_post", "client_secret_basic"}
                or set(client_info.grant_types) != {"authorization_code", "refresh_token"}
                or client_info.response_types != ["code"]
                or client_info.scope != SCOPE or len(client_info.client_name or "") > 200):
            raise RegistrationError("invalid_client_metadata", "Unsupported client authentication, grant or scope")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM clients").fetchone()[0] >= 1000:
                raise RegistrationError("invalid_client_metadata", "Client registration capacity reached")
            db.execute("INSERT INTO clients VALUES (?,?,?)", (client_info.client_id,
                       client_info.model_dump_json(exclude={"client_secret"}),
                       digest(client_info.client_secret) if client_info.client_secret else None))

    async def authorize(self, client, params: AuthorizationParams):
        if (params.resource not in {None, self.resource}
                or params.scopes not in (None, [SCOPE])
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge)):
            raise AuthorizeError("invalid_request", "Invalid resource, scope or S256 challenge")
        params.resource = self.resource
        params.scopes = [SCOPE]
        pending = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM pending WHERE expires<?", (time.time(),))
            db.execute("DELETE FROM grants WHERE expires<?", (time.time(),))
            if db.execute("SELECT COUNT(*) FROM pending").fetchone()[0] >= 500:
                raise AuthorizeError("temporarily_unavailable", "Too many pending authorization requests")
            db.execute("INSERT INTO pending (id,client_id,params,expires) VALUES (?,?,?,?)",
                       (pending, client.client_id, params.model_dump_json(), time.time() + 600))
        return self.issuer + "/oauth/consent?request=" + pending

    def current_participant(self, name, fingerprint, datasets):
        state, _ = self.store.read()
        for user in state.participants:
            if user.name == name and hmac.compare_digest(user.token_sha256, fingerprint):
                permitted = sorted(set(user.datasets) & set(datasets) & {d.id for d in state.catalog.datasets})
                if permitted:
                    return user.model_copy(update={"datasets": permitted})
        return None

    def grant_user(self, row):
        if not row or row["revoked"] or row["grant_expires"] <= time.time():
            return None
        return self.current_participant(row["participant"], row["fingerprint"], json.loads(row["datasets"]))

    def token_row(self, db, token, kind):
        return db.execute("""SELECT t.*, g.client_id, g.participant, g.fingerprint, g.datasets,
                          g.expires AS grant_expires, g.revoked FROM tokens t
                          JOIN grants g ON g.id=t.grant_id WHERE t.hash=? AND t.kind=?""",
                          (digest(token), kind)).fetchone()

    def resolve_participant(self, token):
        if not token.startswith("dl_at_"):
            return None
        with self.db() as db:
            row = self.token_row(db, token, "access")
        if row and not row["used"] and row["expires"] > time.time():
            return self.grant_user(row)
        return None

    async def load_authorization_code(self, client, authorization_code):
        with self.db() as db:
            row = self.token_row(db, authorization_code, "code")
        if (not row or row["used"] or row["expires"] <= time.time()
                or row["client_id"] != client.client_id or not self.grant_user(row)):
            return None
        params = AuthorizationParams.model_validate_json(row["data"])
        return AuthorizationCode(code=authorization_code, client_id=client.client_id,
                                 expires_at=row["expires"], subject=row["participant"], **params.model_dump())

    def issue(self, db, grant_id, grant_expires):
        access, refresh = "dl_at_" + secrets.token_urlsafe(32), "dl_rt_" + secrets.token_urlsafe(32)
        expires = min(int(time.time()) + ACCESS_SECONDS, int(grant_expires))
        db.execute("UPDATE tokens SET used=1 WHERE grant_id=? AND kind='access'", (grant_id,))
        db.executemany("INSERT INTO tokens (hash,kind,grant_id,expires) VALUES (?,?,?,?)",
                       [(digest(access), "access", grant_id, expires),
                        (digest(refresh), "refresh", grant_id, grant_expires)])
        return OAuthToken(access_token=access, refresh_token=refresh, expires_in=max(0, expires-int(time.time())), scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.token_row(db, authorization_code.code, "code")
            if (not row or row["used"] or row["expires"] <= time.time()
                    or row["client_id"] != client.client_id or not self.grant_user(row)):
                raise TokenError("invalid_grant", "Authorization code unavailable")
            db.execute("UPDATE tokens SET used=1 WHERE hash=?", (digest(authorization_code.code),))
            return self.issue(db, row["grant_id"], row["grant_expires"])

    async def load_refresh_token(self, client, refresh_token):
        with self.db() as db:
            row = self.token_row(db, refresh_token, "refresh")
        if not row or row["client_id"] != client.client_id:
            return None
        # Return consumed refresh tokens too: exchange detects reuse and revokes
        # their entire grant, including any access/refresh replacement tokens.
        return RefreshToken(token=refresh_token, client_id=client.client_id, scopes=[SCOPE],
                            expires_at=int(row["expires"]), subject=row["participant"])

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        reused = False
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.token_row(db, refresh_token.token, "refresh")
            if not row or row["client_id"] != client.client_id or scopes != [SCOPE]:
                raise TokenError("invalid_grant", "Refresh token unavailable")
            if row["used"]:
                db.execute("UPDATE grants SET revoked=1 WHERE id=?", (row["grant_id"],))
                reused = True
            elif row["expires"] <= time.time() or not self.grant_user(row):
                raise TokenError("invalid_grant", "Refresh token unavailable")
            else:
                db.execute("UPDATE tokens SET used=1 WHERE hash=?", (digest(refresh_token.token),))
                result = self.issue(db, row["grant_id"], row["grant_expires"])
        if reused:
            raise TokenError("invalid_grant", "Refresh token reuse detected; sign in again")
        return result

    async def load_access_token(self, token):
        with self.db() as db:
            row = self.token_row(db, token, "access")
        if row and not row["used"] and row["expires"] > time.time() and self.grant_user(row):
            return AccessToken(token=token, client_id=row["client_id"], scopes=[SCOPE],
                               expires_at=int(row["expires"]), resource=self.resource, subject=row["participant"])
        return None

    async def revoke_token(self, token):
        self.revoke_raw(token.token, token.client_id)

    def revoke_raw(self, token, client_id):
        with self.db() as db:
            db.execute("""UPDATE grants SET revoked=1 WHERE client_id=? AND id IN
                          (SELECT grant_id FROM tokens WHERE hash=?)""", (client_id, digest(token)))

    def list_grants(self):
        with self.db() as db:
            rows = db.execute("""SELECT g.*, g.expires AS grant_expires, c.metadata FROM grants g
                                 JOIN clients c ON c.id=g.client_id WHERE g.expires>? ORDER BY g.created DESC""",
                              (time.time(),)).fetchall()
        return [{"id": row["id"], "participant": row["participant"],
                 "client_name": json.loads(row["metadata"]).get("client_name") or "OAuth client",
                 "datasets": json.loads(row["datasets"]), "created": row["created"], "expires": row["expires"],
                 "active": bool(self.grant_user(row))} for row in rows]

    def revoke_grant(self, grant_id):
        with self.db() as db:
            db.execute("UPDATE grants SET revoked=1 WHERE id=?", (grant_id,))

    def reconcile(self, state):
        """Revocation and grant reductions must not revive after later edits."""
        users = {user.name: user for user in state.participants}
        ids = {dataset.id for dataset in state.catalog.datasets}
        with self.db() as db:
            for grant in db.execute("SELECT * FROM grants WHERE revoked=0").fetchall():
                user = users.get(grant["participant"])
                permitted = sorted(set(json.loads(grant["datasets"])) & set(user.datasets) & ids) if user else []
                if not user or not hmac.compare_digest(user.token_sha256, grant["fingerprint"]) or not permitted:
                    db.execute("UPDATE grants SET revoked=1 WHERE id=?", (grant["id"],))
                else:
                    db.execute("UPDATE grants SET datasets=? WHERE id=?", (json.dumps(permitted), grant["id"]))

    def cookie_name(self, request_id):
        # Separate browser proofs for simultaneous tabs and client retries.
        return COOKIE + "-" + digest(request_id)[:24]

    def page(self, request_id, message="", status=200, existing_csrf=""):
        with self.db() as db:
            row = db.execute("SELECT * FROM pending WHERE id=? AND expires>? AND attempts<5",
                             (request_id, time.time())).fetchone()
            if not row:
                return HTMLResponse("Authorization request expired. Return to your client and connect again.", status_code=400)
            # Reloading a page must not invalidate its already rendered form.
            csrf = existing_csrf if (existing_csrf and hmac.compare_digest(
                digest(existing_csrf), row["csrf_hash"] or "")) else secrets.token_urlsafe(32)
            db.execute("UPDATE pending SET csrf_hash=? WHERE id=?", (digest(csrf), request_id))
            client = db.execute("SELECT metadata FROM clients WHERE id=?", (row["client_id"],)).fetchone()
        esc = html.escape
        params = AuthorizationParams.model_validate_json(row["params"])
        client_name = json.loads(client["metadata"]).get("client_name") or "OAuth client"
        if row["participant"]:
            datasets = "".join("<li>" + esc(item) + "</li>" for item in json.loads(row["datasets"]))
            body = ("<h2>Review reporting access</h2><p>Participant: <strong>" + esc(row["participant"]) +
                    "</strong></p><p>This application will be able to read all rows in these datasets:</p><ul>" + datasets +
                    "</ul><button name='action' value='approve'>Allow read-only reporting access</button>")
        else:
            body = ("<h2>Sign in to DevLens Reporting</h2><p>Enter the participant token provided by your administrator. "
                    "Your SQL password, administrator token and ChatGPT password are not accepted here.</p>"
                    "<label>Participant token<input name='participant_token' type='password' autocomplete='off' required maxlength='512'></label>"
                    "<button name='action' value='login'>Continue</button>")
        document = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>DevLens Reporting authorization</title><style>body{font:16px system-ui;background:#f3f6fa;color:#172b43;max-width:650px;margin:40px auto;padding:20px}"
                    "main{background:white;padding:28px;border-radius:12px;border:1px solid #dce5ef}p{line-height:1.6;overflow-wrap:anywhere}input,button{font:inherit;padding:12px;box-sizing:border-box}"
                    "input{display:block;width:100%;margin:8px 0 16px}button{max-width:100%;background:#145d9d;color:white;border:0;border-radius:6px;margin:8px 8px 0 0;cursor:pointer}small{color:#526477}"
                    ".error{color:#96331c}</style></head><body><main><h1>DevLens Reporting</h1><p>Instance: " + esc(self.issuer) +
                    "</p><p>Application: <strong>" + esc(client_name) + "</strong><br><small>This name is supplied by the client.</small></p><p>Return address: " +
                    esc(str(params.redirect_uri)) + "</p><p class='error' role='alert'>" + esc(message) + "</p>"
                    "<form method='post' action='/oauth/consent'><input type='hidden' name='request' value='" + esc(request_id) +
                    "'><input type='hidden' name='csrf' value='" + esc(csrf) + "'>" + body +
                    "<button name='action' value='deny' formnovalidate>Deny</button></form>"
                    "<p><small>Access tokens expire after one hour. A refresh grant lasts at most 30 days and can be revoked by your administrator."
                    " Your participant token is not passed to the connecting application.</small></p></main></body></html>")
        # Chrome applies form-action to redirects after form submission too.
        # Permit only self and this request's already validated callback origin.
        callback = urlsplit(str(params.redirect_uri))
        callback_origin = callback.scheme + "://" + callback.netloc
        response = HTMLResponse(document, status_code=status, headers={
            "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' " + callback_origin + "; frame-ancestors 'none'; base-uri 'none'",
            "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"})
        response.set_cookie(self.cookie_name(request_id), csrf, secure=True, httponly=True, samesite="lax", max_age=600, path="/")
        return response

    async def consent(self, request: Request):
        if request.method == "GET":
            if not self.rate_allowed("consent_page", 120, request):
                return oauth_error("temporarily_unavailable", "Try again in a minute", 429)
            request_id = request.query_params.get("request", "")
            return self.page(request_id, existing_csrf=request.cookies.get(self.cookie_name(request_id), ""))
        if not self.rate_allowed("consent_submit", 60, request):
            return oauth_error("temporarily_unavailable", "Try again in a minute", 429)
        if request.headers.get("origin") not in {None, self.issuer}:
            return oauth_error("access_denied", "Use this instance's consent page", 403)
        form = await request.form()
        request_id, csrf = str(form.get("request", "")), str(form.get("csrf", ""))
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pending WHERE id=? AND expires>? AND attempts<5", (request_id, time.time())).fetchone()
            cookie = request.cookies.get(self.cookie_name(request_id), "")
            reason = ("request_expired_or_used" if not row else
                      "csrf_mismatch" if not csrf or not hmac.compare_digest(digest(csrf), row["csrf_hash"] or "") else
                      "cookie_missing" if not cookie else
                      "cookie_mismatch" if not hmac.compare_digest(csrf.encode(), cookie.encode()) else None)
            if reason:
                # Log only a category, never credentials or form identifiers.
                logging.getLogger(__name__).warning("OAuth consent rejected: %s", reason)
                return oauth_error("access_denied", "Consent verification failed; restart sign-in (" + reason + ")", 403)
            params = AuthorizationParams.model_validate_json(row["params"])
            action = form.get("action")
            if action == "deny":
                db.execute("DELETE FROM pending WHERE id=?", (request_id,))
                redirect = construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state, iss=self.issuer)
            elif action == "login":
                token = str(form.get("participant_token", ""))
                proof = digest(token)
                state, _ = self.store.read()
                user = next((u for u in state.participants if hmac.compare_digest(u.token_sha256, proof)), None)
                db.execute("UPDATE pending SET attempts=attempts+1 WHERE id=?", (request_id,))
                if not user or not user.datasets:
                    message = "Invalid participant token or no dataset access."
                else:
                    db.execute("UPDATE pending SET participant=?, fingerprint=?, datasets=? WHERE id=?",
                               (user.name, user.token_sha256, json.dumps(user.datasets), request_id))
                    message = ""
                redirect = None
            elif action == "approve" and row["participant"]:
                user = self.current_participant(row["participant"], row["fingerprint"], json.loads(row["datasets"]))
                if not user:
                    return oauth_error("access_denied", "Participant access changed; restart sign-in", 403)
                if db.execute("SELECT COUNT(*) FROM grants WHERE revoked=0 AND expires>?", (time.time(),)).fetchone()[0] >= 1000:
                    return oauth_error("temporarily_unavailable", "Authorization capacity reached", 429)
                grant_id, code = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
                now = time.time()
                db.execute("INSERT INTO grants (id,client_id,participant,fingerprint,datasets,created,expires) VALUES (?,?,?,?,?,?,?)",
                           (grant_id, row["client_id"], user.name, user.token_sha256, json.dumps(user.datasets), now, now + GRANT_SECONDS))
                db.execute("INSERT INTO tokens (hash,kind,grant_id,expires,data) VALUES (?,'code',?,?,?)",
                           (digest(code), grant_id, now + 120, row["params"]))
                db.execute("DELETE FROM pending WHERE id=?", (request_id,))
                redirect = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state, iss=self.issuer)
            else:
                return oauth_error("invalid_request", "Invalid consent action")
        if redirect is None:
            return self.page(request_id, message, status=401 if message else 200, existing_csrf=csrf)
        response = RedirectResponse(redirect, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.delete_cookie(self.cookie_name(request_id), secure=True, httponly=True, samesite="lax", path="/")
        return response

    def metadata(self):
        return {"issuer": self.issuer, "authorization_endpoint": self.issuer + "/authorize",
                "token_endpoint": self.issuer + "/token", "registration_endpoint": self.issuer + "/register",
                "revocation_endpoint": self.issuer + "/revoke", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"], "scopes_supported": [SCOPE],
                "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
                "code_challenge_methods_supported": ["S256"], "authorization_response_iss_parameter_supported": True,
                "client_id_metadata_document_supported": False}

    def routes(self):
        authenticator = HashedClientAuthenticator(self)
        registration = RegistrationHandler(self, ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]))
        authorization = AuthorizationHandler(self)
        token_handler = TokenHandler(self, authenticator)

        async def metadata(request):
            return JSONResponse(self.metadata())

        async def resource_metadata(request):
            return JSONResponse({"resource": self.resource, "authorization_servers": [self.issuer],
                                 "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"], "resource_name": "DevLens Reporting"})

        async def register(request):
            if not self.rate_allowed("register", 30, request):
                return oauth_error("temporarily_unavailable", "Try registration again later", 429)
            try:
                return await registration.handle(request)
            except (ValueError, TypeError):
                return oauth_error("invalid_client_metadata", "Invalid client registration")

        async def authorize(request):
            if not self.rate_allowed("authorize", 60, request):
                return oauth_error("temporarily_unavailable", "Try authorization again later", 429)
            response = await authorization.handle(request)
            target = response.headers.get("location")
            if target and not target.startswith(self.issuer + "/oauth/consent?"):
                response.headers["location"] = construct_redirect_uri(target, iss=self.issuer)
            return response

        async def token(request):
            if not self.rate_allowed("token", 120, request):
                return oauth_error("temporarily_unavailable", "Try the token exchange again later", 429)
            await authenticator.normalize_form(request)
            form = await request.form()
            if form.get("resource") not in {None, self.resource}:
                return oauth_error("invalid_target", "Resource does not match this reporting instance")
            if form.get("grant_type") == "authorization_code" and not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", str(form.get("code_verifier", ""))):
                return oauth_error("invalid_grant", "Invalid PKCE verifier")
            return await token_handler.handle(request)

        async def revoke(request):
            if not self.rate_allowed("revoke", 120, request):
                return oauth_error("temporarily_unavailable", "Try revocation again later", 429)
            await authenticator.normalize_form(request)
            try:
                client = await authenticator.authenticate_request(request)
            except AuthenticationError:
                return oauth_error("invalid_client", "Client authentication failed", 401)
            form = await request.form()
            if not isinstance(form.get("token"), str):
                return oauth_error("invalid_request", "A token is required")
            self.revoke_raw(form["token"], client.client_id)
            return JSONResponse({})

        return [Route("/.well-known/oauth-authorization-server", metadata),
                Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
                Route("/.well-known/oauth-protected-resource", resource_metadata),
                Route("/register", register, methods=["POST"]),
                Route("/authorize", authorize, methods=["GET", "POST"]),
                Route("/token", token, methods=["POST"]), Route("/revoke", revoke, methods=["POST"]),
                Route("/oauth/consent", self.consent, methods=["GET", "POST"])]


class HashedClientAuthenticator:
    """SDK handler adapter: high-entropy client secrets are hashed at rest."""
    def __init__(self, provider):
        self.provider = provider

    @staticmethod
    def basic_credentials(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return None
        from urllib.parse import unquote
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            client_id, secret = decoded.split(":", 1)
            return unquote(client_id), unquote(secret)
        except (ValueError, UnicodeDecodeError):
            raise AuthenticationError("Invalid Basic credentials") from None

    async def normalize_form(self, request):
        # SDK token models require client_id in the form, including Basic clients.
        form = dict(await request.form())
        try:
            credentials = self.basic_credentials(request)
        except AuthenticationError:
            credentials = None
        if credentials and not form.get("client_id"):
            form["client_id"] = credentials[0]
        request._form = FormData(form)

    async def authenticate_request(self, request):
        form = await request.form()
        client = await self.provider.get_client(str(form.get("client_id", "")))
        if not client:
            raise AuthenticationError("Invalid client credentials")
        with self.provider.db() as db:
            row = db.execute("SELECT secret_hash FROM clients WHERE id=?", (client.client_id,)).fetchone()
        method = client.token_endpoint_auth_method
        if method == "none":
            if row["secret_hash"]:
                raise AuthenticationError("Invalid client credentials")
            return client
        if method == "client_secret_basic":
            credentials = self.basic_credentials(request)
            if not credentials or credentials[0] != client.client_id:
                raise AuthenticationError("Invalid client credentials")
            secret = credentials[1]
        elif method == "client_secret_post":
            secret = str(form.get("client_secret", ""))
        else:
            raise AuthenticationError("Invalid client authentication method")
        if not secret or not row["secret_hash"] or not hmac.compare_digest(digest(secret), row["secret_hash"]):
            raise AuthenticationError("Invalid client credentials")
        if client.client_secret_expires_at and client.client_secret_expires_at < time.time():
            raise AuthenticationError("Client credentials expired")
        return client
