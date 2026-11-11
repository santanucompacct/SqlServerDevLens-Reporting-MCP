import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from devlens_reporting.app import create_app
from devlens_reporting.models import Catalog, Query, State
from devlens_reporting.query import compile_query
from devlens_reporting.storage import Store

TOKEN = "participant-test-token-with-sufficient-entropy"
ADMIN = "administrator-test-token-with-sufficient-entropy"


@pytest.fixture
def state(tmp_path):
    from pathlib import Path
    value = json.loads(Path("examples/instance.json").read_text())
    value["participants"] = [{"name": "tester", "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(), "datasets": ["sales"]}]
    path = tmp_path / "instance.json"
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def client(state, monkeypatch):
    monkeypatch.setenv("DEVLENS_ADMIN_TOKEN", ADMIN)
    with TestClient(create_app(str(state), demo=True), base_url="http://localhost") as value:
        yield value


def headers(token=ADMIN):
    return {"Authorization": "Bearer " + token}


def rpc(client, method, params=None):
    return client.post("/mcp", headers={**headers(TOKEN), "Accept": "application/json, text/event-stream",
                                        "MCP-Protocol-Version": "2025-06-18"},
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_authentication_and_origin(client):
    assert client.get("/admin/api/state").status_code == 401
    assert client.post("/mcp", json={}).status_code == 401
    assert client.get("/admin").status_code == 200
    assert client.get("/health").json()["demo"] is True
    assert client.post("/admin/api/test", headers={**headers(), "Origin": "https://attacker.example"},
                       json={"connection": {}}).status_code == 403
    assert client.post("/admin/api/test", headers={**headers(), "Origin": "http://localhost"},
                       json={"connection": {}}).status_code == 200


def test_parameterization_and_aggregates(state):
    dataset = Store(state).read()[0].catalog.datasets[0]
    malicious = "x'; DROP TABLE Sales; --"
    sql, values = compile_query(dataset, Query(dataset="sales", fields=["customer"],
                                              filters=[{"column": "customer", "op": "eq", "value": malicious}]))
    assert malicious not in sql
    assert values == [malicious]
    with pytest.raises(ValueError):
        compile_query(dataset, Query(dataset="sales", fields=["unpublished"]))
    with pytest.raises(ValueError):
        compile_query(dataset, Query(dataset="sales", measures=[{"column": "customer", "function": "sum", "alias": "total"}]))
    with pytest.raises(ValueError):
        compile_query(dataset, Query(dataset="sales", fields=["amount"], filters=[{"column": "amount", "op": "eq", "value": "1 OR 1=1"}]))
    with pytest.raises(ValueError):
        Query(dataset="sales", fields=["amount];DROP"])


def test_query_shapes_and_limits(client):
    result = client.post("/admin/api/preview", headers=headers(), json={"dataset": "sales", "limit": 1})
    assert result.status_code == 200
    assert len(result.json()["rows"]) == 1
    assert result.json()["truncated"] is True
    result = client.post("/admin/api/preview", headers=headers(), json={"dataset": "sales", "group_by": ["customer"],
        "measures": [{"column": "amount", "function": "sum", "alias": "total"}]})
    assert result.json()["rows"] == [{"customer": "Example A", "total": 250.0}, {"customer": "Example B", "total": 250.0}]
    result = client.post("/admin/api/preview", headers=headers(), json={"dataset": "sales", "fields": ["amount"],
        "filters": [{"column": "branch", "op": "eq", "value": "West"}]})
    assert result.json()["rows"] == [{"amount": 250.0}]
    assert client.post("/admin/api/preview", headers=headers(), json={"dataset": "sales", "limit": 1001}).status_code == 422


def test_publish_and_stale_revision(client):
    original = client.get("/admin/api/state", headers=headers()).json()
    changed = copy.deepcopy(original["state"])
    changed["catalog"]["version"] = "v2"
    body = {"revision": original["revision"], "state": changed}
    assert client.post("/admin/api/publish", headers=headers(), json=body).status_code == 200
    assert client.post("/admin/api/publish", headers=headers(), json=body).status_code == 409
    current = client.get("/admin/api/state", headers=headers()).json()
    assert current["state"]["catalog"]["version"] == "v2"
    changed["catalog"]["datasets"][0]["object_name"] = "Missing"
    body = {"revision": current["revision"], "state": changed}
    assert client.post("/admin/api/publish", headers=headers(), json=body).status_code == 400
    assert client.get("/admin/api/state", headers=headers()).json()["revision"] == current["revision"]


def test_mcp_tools_and_revocation(client):
    response = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "1"}})
    assert response.status_code == 200
    response = rpc(client, "tools/list")
    assert response.status_code == 200
    assert {t["name"] for t in response.json()["result"]["tools"]} == {"list_datasets", "describe_dataset", "query_dataset"}
    response = rpc(client, "tools/call", {"name": "list_datasets", "arguments": {}})
    assert not response.json()["result"].get("isError")
    response = rpc(client, "tools/call", {"name": "query_dataset", "arguments": {"query": {"dataset": "sales", "fields": ["amount"]}}})
    assert not response.json()["result"].get("isError"), response.text
    original = client.get("/admin/api/state", headers=headers()).json()
    original["state"]["participants"][0]["datasets"] = []
    assert client.post("/admin/api/publish", headers=headers(), json={"revision": original["revision"], "state": original["state"]}).status_code == 200
    response = rpc(client, "tools/call", {"name": "query_dataset", "arguments": {"query": {"dataset": "sales"}}})
    assert response.json()["result"]["isError"] is True
    original = client.get("/admin/api/state", headers=headers()).json()
    original["state"]["participants"] = []
    client.post("/admin/api/publish", headers=headers(), json={"revision": original["revision"], "state": original["state"]})
    assert rpc(client, "tools/list").status_code == 401


def test_concurrent_publish(state):
    store = Store(state)
    model, revision = store.read()
    def publish():
        try:
            changed = model.model_copy(deep=True)
            changed.catalog.version = "changed"
            store.publish(changed, revision)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: publish(), range(2))) == [False, True]


def test_request_size_and_host_protection(client):
    response = client.post('/admin/api/publish', headers=headers(), content=b'x' * 262145)
    assert response.status_code == 413
    response = client.post('/mcp', headers={**headers(TOKEN), 'Host': 'attacker.example',
        'Accept': 'application/json, text/event-stream'}, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert response.status_code == 421


def test_separate_catalog_and_failed_manifest_publish(state, monkeypatch):
    import devlens_reporting.storage as storage
    store = Store(state)
    model, revision = store.read()
    model.catalog.version = 'separate-catalog'
    new_revision = store.publish(model, revision)
    manifest = json.loads(state.read_text())
    assert 'catalog' not in manifest
    assert json.loads((state.parent / manifest['catalog_file']).read_text())['version'] == 'separate-catalog'
    assert store.read()[0].catalog.version == 'separate-catalog'
    original_write = storage.atomic_write
    def fail_manifest(path, raw):
        if path == state:
            raise OSError('Simulated disk failure')
        original_write(path, raw)
    monkeypatch.setattr(storage, 'atomic_write', fail_manifest)
    model.catalog.version = 'must-not-publish'
    with pytest.raises(OSError):
        store.publish(model, new_revision)
    assert store.read()[0].catalog.version == 'separate-catalog'


def test_hyphenated_instance_startup(state, monkeypatch):
    value = json.loads(state.read_text())
    value["instance"] = "lalbaba-instance"
    state.write_text(json.dumps(value))
    monkeypatch.setenv("DEVLENS_ADMIN_TOKEN", ADMIN)
    with TestClient(create_app(str(state), demo=True), base_url="http://localhost") as client:
        assert client.get("/health").status_code == 200
        loaded = client.get("/admin/api/state", headers=headers()).json()
        assert loaded["state"]["instance"] == "lalbaba-instance"
    with pytest.raises(ValueError):
        Query(dataset="sales", fields=["column-with-hyphen"])


def test_save_sql_password_special_characters(client, state):
    password = "  aBc123#;{}'\" \u03c8\r\nend  "
    assert client.post("/admin/api/password", json={"password": password}).status_code == 401
    result = client.post("/admin/api/password", headers=headers(), json={"password": password})
    assert result.status_code == 200
    assert result.json() == {"status": "saved"}
    stored = state.parent / "sql-password"
    assert stored.read_bytes().decode("utf-8") == password
    assert stored.stat().st_mode & 0o777 == 0o600
    assert password not in client.get("/admin/api/state", headers=headers()).text
    assert password.encode() not in state.read_bytes()
    assert client.post("/admin/api/password", headers=headers(), json={"password": ""}).status_code == 400
    assert client.post("/admin/api/password", headers=headers(), json={"password": "abc\x00def"}).status_code == 400
    assert stored.read_bytes().decode("utf-8") == password


def test_saved_password_odbc_escaping_and_environment_fallback(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from devlens_reporting.database import Database, odbc_value
    from devlens_reporting.models import Connection
    captured = []
    class FakeConnection:
        timeout = 0
        def close(self):
            pass
    def connect(dsn, **kwargs):
        captured.append(dsn)
        return FakeConnection()
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect))
    monkeypatch.setenv("DEVLENS_SQL_PASSWORD", "legacy-environment-password")
    settings = Connection(server="sql.example.com", database="test", username="reporting")
    password_path = tmp_path / "sql-password"
    database = Database(password_path=password_path)
    with database.connect(settings):
        pass
    assert "PWD={legacy-environment-password};" in captured[-1]
    password = " spaces#;{}'\"\u03c8 "
    password_path.write_bytes(password.encode("utf-8"))
    with database.connect(settings):
        pass
    assert "PWD=" + odbc_value(password) + ";" in captured[-1]
    assert "legacy-environment-password" not in captured[-1]
    assert "Encrypt=yes;TrustServerCertificate=no;" in captured[-1]


@pytest.mark.parametrize("encrypt,trust", [(True, False), (True, True), (False, False), (False, True)])
def test_sql_tls_settings_reach_odbc(encrypt, trust, monkeypatch):
    import sys
    from types import SimpleNamespace
    from devlens_reporting.database import Database
    from devlens_reporting.models import Connection
    captured = []
    class FakeConnection:
        timeout = 0
        def close(self):
            pass
    def connect(dsn, **kwargs):
        captured.append(dsn)
        return FakeConnection()
    monkeypatch.setitem(sys.modules, "pyodbc", SimpleNamespace(connect=connect))
    monkeypatch.setenv("DEVLENS_SQL_PASSWORD", "synthetic-password")
    settings = Connection(server="example", database="example", username="reader",
                          encrypt=encrypt, trust_server_certificate=trust)
    with Database().connect(settings):
        pass
    assert "Encrypt=" + ("yes" if encrypt else "no") + ";" in captured[0]
    assert "TrustServerCertificate=" + ("yes" if trust else "no") + ";" in captured[0]


def test_legacy_tls_defaults_and_diagnostic_redaction():
    from devlens_reporting.models import Connection
    from devlens_reporting.database import connection_error_message
    settings = Connection.model_validate({"server": "example", "database": "example", "username": "reader"})
    assert settings.encrypt is True
    assert settings.trust_server_certificate is False
    cases = [
        (Exception("08001", "SSL Provider: certificate verify failed:self-signed certificate; secret=hidden"), "certificate validation failed"),
        (Exception("28000", "Login failed for secret-user (18456)"), "SQL login failed"),
        (Exception("42000", "Cannot open private-db (4060)"), "selected database"),
        (Exception("42000", "SELECT permission denied on private-view (229)"), "SQL permission denied"),
        (Exception("HYT00", "Login timeout expired for private-host"), "timed out"),
        (Exception("08001", "TCP Provider could not reach private-host"), "could not be reached"),
    ]
    for error, expected in cases:
        message = connection_error_message(error)
        assert expected in message
        assert not any(secret in message for secret in ["hidden", "secret-user", "private-db", "private-view", "private-host"])
    assert "metadata inspection failed" in connection_error_message(Exception("unknown"), "metadata")


def test_connection_error_is_actionable_and_redacted(client, monkeypatch):
    from devlens_reporting.database import Database
    def fail(self, settings, catalog):
        raise Exception("08001", "SSL Provider: certificate verify failed; password=do-not-return")
    monkeypatch.setattr(Database, "validate", fail)
    result = client.post("/admin/api/test", headers=headers(), json={"connection": {"encrypt": True, "trust_server_certificate": False}})
    assert result.status_code == 400
    assert "certificate validation failed" in result.json()["detail"]
    assert "do-not-return" not in result.text


def test_metadata_diagnostic_identifies_failure_without_provider_secrets(client, monkeypatch):
    from devlens_reporting.database import Database
    class ProgrammingError(Exception):
        pass
    def fail(self, settings):
        raise ProgrammingError("42S22", "Invalid column on private-host; password=never-return")
    monkeypatch.setattr(Database, "objects", fail)
    result = client.post("/admin/api/test", headers=headers(), json={"connection": {}})
    assert result.status_code == 400
    assert "ProgrammingError / SQLSTATE 42S22" in result.json()["detail"]
    assert "never-return" not in result.text
    assert "private-host" not in result.text
