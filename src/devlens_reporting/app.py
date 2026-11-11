"""One independent reporting instance per process and configuration file."""
import hashlib
import hmac
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, SecretStr
from starlette.concurrency import run_in_threadpool
from .limits import RequestSizeLimit
from .database import Database, connection_error_message
from .models import Catalog, Column, Connection, Dataset, Model, Name, Participant, Query, State
from .query import compile_query
from .storage import Store, atomic_write
from .setup import SetupStore, object_key

log = logging.getLogger("devlens_reporting")


class Publish(Model):
    revision: str
    state: State


class PasswordUpdate(Model):
    password: SecretStr


class Inspect(Model):
    connection: Connection
    schema_name: Name | None = None
    object_name: Name | None = None


class ConnectionSave(Model):
    revision: str
    connection: Connection


class FieldRemark(Model):
    name: str = Field(min_length=1, max_length=128)
    remarks: str = Field(default="", max_length=2000)


class ObjectSave(Model):
    revision: str
    connection: Connection
    schema_name: Name
    object_name: Name
    columns: list[FieldRemark] = Field(max_length=1024)


class PrepareDataset(Model):
    revision: str
    connection: Connection
    schema_name: Name
    object_name: Name
    dataset_id: Name
    description: str = Field(min_length=1, max_length=4000)
    grain: str = Field(min_length=1, max_length=2000)
    business_rules: list[str] = Field(default_factory=list, max_length=50)


class ParticipantCreate(Model):
    revision: str
    name: Name
    datasets: list[Name] = Field(min_length=1, max_length=100)


class ParticipantRevoke(Model):
    revision: str
    name: Name


class ParticipantTest(Model):
    token: SecretStr


def create_app(state_path: str | None = None, demo: bool | None = None) -> FastAPI:
    store = Store(Path(state_path or os.environ.get("DEVLENS_STATE", "state/instance.json")))
    password_path = store.path.parent / "sql-password"
    setup = SetupStore(store.path.parent / "setup.json")
    database = Database(demo if demo is not None else os.environ.get("DEVLENS_DEMO") == "1",
                        password_path=password_path)
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

    async def execute(state, user, query, diagnostics=False):
        dataset = visible_dataset(state, user, query.dataset)
        if dataset is None:
            raise ValueError("Dataset unavailable")
        sql, values = compile_query(dataset, query)
        async with query_slots:
            try:
                rows, truncated = await run_in_threadpool(database.execute, state.connection, sql, values, query.limit)
            except ValueError:
                raise
            except Exception as error:
                log.warning("instance=%s user=%s dataset=%s outcome=database_error", state.instance, user.name, dataset.id)
                if diagnostics:
                    raise ValueError(connection_error_message(error, "query")) from None
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
    def get_state(request: Request):
        state, revision = store.read()
        public_host = next((host for host in hosts if not host.startswith(("127.", "localhost", "[::1]"))
                            and "*" not in host), None)
        endpoint = ("https://" + public_host if public_host else str(request.base_url).rstrip("/")) + "/mcp"
        return {"state": state.model_dump(), "revision": revision, "demo": database.demo,
                "setup": setup.configuration(state.connection),
                "mcp_url": endpoint,
                "password_saved": password_path.exists() or bool(os.environ.get(state.connection.password_env))}

    @app.post("/admin/api/connection")
    def save_connection(body: ConnectionSave):
        if not all((body.connection.server.strip(), body.connection.database.strip(), body.connection.username.strip())):
            raise HTTPException(400, "Enter the SQL server, database and username before saving")
        try:
            revision = setup.save_connection(body.connection, body.revision)
            return {"status": "saved", "revision": revision}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/admin/api/object")
    async def inspect_object(body: Inspect):
        if not body.schema_name or not body.object_name:
            raise HTTPException(400, "Select an object first")
        try:
            current = await run_in_threadpool(database.columns, body.connection, body.schema_name, body.object_name)
            if not current:
                raise HTTPException(400, "No visible columns. Verify the object and SELECT permission, then test SQL again.")
            columns, revision = setup.columns(body.connection, body.schema_name, body.object_name, current)
            return {"columns": columns, "revision": revision, "demo": database.demo}
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(400, connection_error_message(error, "metadata")) from None

    @app.post("/admin/api/object/save")
    async def save_object(body: ObjectSave):
        try:
            current = await run_in_threadpool(database.columns, body.connection, body.schema_name, body.object_name)
        except Exception as error:
            raise HTTPException(400, connection_error_message(error, "metadata")) from None
        remarks = {column.name: column.remarks for column in body.columns}
        if (not current or len(remarks) != len(body.columns)
                or set(remarks) != {column["name"] for column in current}):
            raise HTTPException(409, "Object columns changed; select the object again before saving remarks")
        columns = [{**column, "remarks": remarks[column["name"]]} for column in current]
        try:
            revision = setup.save_object(body.connection, body.schema_name, body.object_name, columns, body.revision)
            return {"status": "saved", "revision": revision, "columns": columns}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/admin/api/password")
    def save_password(body: PasswordUpdate):
        value = body.password.get_secret_value()
        if not value or "\x00" in value:
            raise HTTPException(400, "Enter a nonempty SQL password without null characters")
        try:
            atomic_write(password_path, value.encode("utf-8"))
        except OSError:
            raise HTTPException(500, "The SQL password could not be saved") from None
        return {"status": "saved"}

    @app.post("/admin/api/dataset/prepare")
    async def prepare_dataset(body: PrepareDataset):
        saved, revision = setup.read()
        if revision != body.revision:
            raise HTTPException(409, "Saved setup changed; reload the object before preparing")
        if Connection.model_validate(saved.get("connection", {})).model_dump() != body.connection.model_dump():
            raise HTTPException(409, "Save SQL details before preparing the dataset")
        dictionary = saved.get("objects", {}).get(object_key(body.connection, body.schema_name, body.object_name))
        if not dictionary:
            raise HTTPException(400, "Save field remarks for this object first")
        try:
            current = await run_in_threadpool(database.columns, body.connection, body.schema_name, body.object_name)
        except Exception as error:
            raise HTTPException(400, connection_error_message(error, "metadata")) from None
        signature = lambda columns: {(c["name"], c["sql_type"], c["nullable"]) for c in columns}
        if not current or signature(current) != signature(dictionary["columns"]):
            raise HTTPException(409, "SQL metadata changed; reload the object and save its remarks again")
        if len(current) > 100:
            raise HTTPException(400, "A reporting dataset supports at most 100 columns")
        types = {"text": {"char", "varchar", "nchar", "nvarchar", "text", "ntext", "uniqueidentifier", "xml"},
                 "number": {"tinyint", "smallint", "int", "bigint", "decimal", "numeric", "money", "smallmoney", "float", "real"},
                 "date": {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time"},
                 "boolean": {"bit"}}
        columns = []
        for column in dictionary["columns"]:
            kind = next((kind for kind, names in types.items() if column["sql_type"].lower() in names), None)
            if kind is None:
                raise HTTPException(400, "Unsupported SQL type for column " + column["name"])
            if not column.get("remarks", "").strip():
                raise HTTPException(400, "Enter and save remarks for column " + column["name"])
            columns.append(Column(name=column["name"], description=column["remarks"], kind=kind))
        dataset = Dataset(id=body.dataset_id, schema_name=body.schema_name, object_name=body.object_name,
                          description=body.description, grain=body.grain,
                          business_rules=body.business_rules, columns=columns)
        return {"dataset": dataset.model_dump(), "setup_revision": revision}

    @app.post("/admin/api/participants/create")
    def create_participant(body: ParticipantCreate):
        state, revision = store.read()
        if revision != body.revision:
            raise HTTPException(409, "Published configuration changed; load it again")
        if any(user.name == body.name for user in state.participants):
            raise HTTPException(409, "Participant name already exists. Revoke it before replacing its token.")
        if set(body.datasets) - {dataset.id for dataset in state.catalog.datasets}:
            raise HTTPException(400, "Choose only published datasets")
        if len(state.participants) >= 100:
            raise HTTPException(400, "At most 100 participants are supported")
        token = secrets.token_urlsafe(32)
        state.participants.append(Participant(name=body.name, datasets=list(dict.fromkeys(body.datasets)),
                                             token_sha256=hashlib.sha256(token.encode()).hexdigest()))
        try:
            revision = store.publish(state, revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"status": "active", "revision": revision, "token": token}

    @app.post("/admin/api/participants/revoke")
    def revoke_participant(body: ParticipantRevoke):
        state, revision = store.read()
        if revision != body.revision:
            raise HTTPException(409, "Published configuration changed; load it again")
        if not any(user.name == body.name for user in state.participants):
            raise HTTPException(404, "Participant unavailable")
        state.participants = [user for user in state.participants if user.name != body.name]
        try:
            revision = store.publish(state, revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"status": "revoked", "revision": revision}

    @app.post("/admin/api/participants/test")
    async def test_participant(body: ParticipantTest):
        state, _ = store.read()
        supplied = Request({"type": "http", "headers": [
            (b"authorization", ("Bearer " + body.token.get_secret_value()).encode("utf-8"))]})
        user = participant(supplied, state)
        datasets = [dataset for dataset in state.catalog.datasets if dataset.id in user.datasets]
        if not datasets:
            raise HTTPException(400, "Participant has no published dataset access")
        dataset = datasets[0]
        try:
            result = await execute(state, user, Query(dataset=dataset.id,
                                   fields=[column.name for column in dataset.columns[:5]], limit=3))
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return {"status": "passed", "participant": user.name, "datasets": user.datasets, "preview": result}

    @app.post("/admin/api/test")
    async def test_connection(body: Inspect):
        stage = "connection"
        try:
            await run_in_threadpool(database.validate, body.connection, Catalog(version="test"))
            stage = "metadata"
            objects = await run_in_threadpool(database.objects, body.connection)
            columns = []
            if body.schema_name and body.object_name:
                columns = await run_in_threadpool(database.columns, body.connection, body.schema_name, body.object_name)
            return {"status": "ok", "demo": database.demo, "objects": objects, "columns": columns}
        except Exception as error:
            raise HTTPException(400, connection_error_message(error, stage)) from None

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
            return await execute(state, admin_user, query, diagnostics=True)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    app.add_middleware(RequestSizeLimit)
    app.mount("/", mcp.streamable_http_app())
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("DEVLENS_PORT", "8101")),
                proxy_headers=False, log_level="info")
