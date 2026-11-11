"""One independent reporting instance per process and configuration file."""
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .limits import RequestSizeLimit
from .database import Database
from .models import Catalog, Connection, Model, Name, Participant, Query, State
from .query import compile_query
from .storage import Store

log = logging.getLogger("devlens_reporting")


class Publish(Model):
    revision: str
    state: State


class Inspect(Model):
    connection: Connection
    schema_name: Name | None = None
    object_name: Name | None = None


def create_app(state_path: str | None = None, demo: bool | None = None) -> FastAPI:
    store = Store(Path(state_path or os.environ.get("DEVLENS_STATE", "state/instance.json")))
    database = Database(demo if demo is not None else os.environ.get("DEVLENS_DEMO") == "1")
    admin_token = os.environ.get("DEVLENS_ADMIN_TOKEN", "")
    if len(admin_token) < 32:
        raise ValueError("DEVLENS_ADMIN_TOKEN must contain at least 32 characters")
    query_slots = anyio.CapacityLimiter(2)
    # Public domains must be explicitly configured for DNS rebinding protection.
    hosts = os.environ.get("DEVLENS_ALLOWED_HOSTS", "127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*").split(",")
    mcp = FastMCP("DevLens Reporting", stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                      allowed_origins=[]))

    def participant(request: Request, state: State) -> Participant:
        supplied = request.headers.get("authorization", "")
        digest = hashlib.sha256(supplied.removeprefix("Bearer ").encode()).hexdigest()
        if supplied.startswith("Bearer "):
            for user in state.participants:
                if hmac.compare_digest(digest, user.token_sha256):
                    return user
        raise HTTPException(401, "Invalid participant token", headers={"WWW-Authenticate": "Bearer"})

    def current_context():
        context = mcp.get_context()
        request = context.request_context.request
        state, _ = store.read()
        return state, participant(request, state)

    def visible_dataset(state, user, dataset_id):
        if dataset_id not in user.datasets:
            raise ValueError("Dataset unavailable")
        return next((d for d in state.catalog.datasets if d.id == dataset_id), None)

    async def execute(state, user, query):
        dataset = visible_dataset(state, user, query.dataset)
        if dataset is None:
            raise ValueError("Dataset unavailable")
        sql, values = compile_query(dataset, query)
        async with query_slots:
            try:
                rows, truncated = await run_in_threadpool(database.execute, state.connection, sql, values, query.limit)
            except ValueError:
                raise
            except Exception:
                log.warning("instance=%s user=%s dataset=%s outcome=database_error", state.instance, user.name, dataset.id)
                raise ValueError("Database query failed; ask the administrator to check connectivity and schema") from None
        log.info("instance=%s user=%s dataset=%s rows=%d", state.instance, user.name, dataset.id, len(rows))
        return {"instance": state.instance, "dataset": dataset.id, "catalog_version": state.catalog.version,
                "query": query.model_dump(), "rows": rows, "truncated": truncated,
                "consistency": "Single SELECT at database configured isolation; separate calls may observe different data"}

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    def list_datasets() -> list[dict]:
        """List datasets this participant can query, with row grain and business purpose."""
        state, user = current_context()
        return [{"id": d.id, "description": d.description, "grain": d.grain}
                for d in state.catalog.datasets if d.id in user.datasets]

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    def describe_dataset(dataset: str) -> dict:
        """Read approved columns, relationships and calculation rules before querying."""
        state, user = current_context()
        definition = visible_dataset(state, user, dataset)
        if definition is None:
            raise ValueError("Dataset unavailable")
        result = definition.model_dump()
        result["relationships"] = [r for r in result["relationships"] if r["target_dataset"] in user.datasets]
        return result

    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False})
    async def query_dataset(query: Query) -> dict:
        """Query one approved dataset. Use fields for details or group_by and measures for totals.

        Filters combine with AND. Date values use ISO format. Results are capped at 1000
        rows and 2 MB. Monetary decimals are exact strings. Read business rules first.
        """
        state, user = current_context()
        return await execute(state, user, query)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def access(request: Request, call_next):
        path = request.url.path
        if path.startswith("/admin/api"):
            token = request.headers.get("authorization", "")
            if not hmac.compare_digest(token, "Bearer " + admin_token):
                return JSONResponse({"detail": "Administrator authentication required"}, status_code=401)
            # Prevent cross-origin browser configuration writes, including trusted proxies.
            if request.headers.get("origin") and request.headers["origin"] != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Use the private same-origin administration page"}, status_code=403)
        if path == "/mcp" or path.startswith("/mcp/"):
            try:
                state, _ = store.read()
                participant(request, state)
            except HTTPException as error:
                return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "demo": database.demo}

    @app.get("/admin")
    def admin():
        return FileResponse(Path(__file__).parent / "static" / "admin.html")

    @app.get("/admin/api/state")
    def get_state():
        state, revision = store.read()
        return {"state": state.model_dump(), "revision": revision, "demo": database.demo}

    @app.post("/admin/api/test")
    async def test_connection(body: Inspect):
        try:
            await run_in_threadpool(database.validate, body.connection, Catalog(version="test"))
            objects = await run_in_threadpool(database.objects, body.connection)
            columns = []
            if body.schema_name and body.object_name:
                columns = await run_in_threadpool(database.columns, body.connection, body.schema_name, body.object_name)
            return {"status": "ok", "objects": objects, "columns": columns}
        except Exception:
            raise HTTPException(400, "Connection or metadata inspection failed; verify SQL settings, TLS and permissions") from None

    @app.post("/admin/api/publish")
    async def publish(body: Publish):
        try:
            await run_in_threadpool(database.validate, body.state.connection, body.state.catalog)
            revision = store.publish(body.state, body.revision)
            return {"status": "published", "revision": revision}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except Exception:
            raise HTTPException(400, "Schema validation failed; draft has not been published") from None

    @app.post("/admin/api/preview")
    async def preview(query: Query):
        state, _ = store.read()
        admin_user = Participant(name="administrator", token_sha256="0" * 64,
                                 datasets=[d.id for d in state.catalog.datasets])
        try:
            return await execute(state, admin_user, query)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    app.add_middleware(RequestSizeLimit)
    app.mount("/", mcp.streamable_http_app())
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("DEVLENS_PORT", "8101")),
                proxy_headers=False, log_level="info")
