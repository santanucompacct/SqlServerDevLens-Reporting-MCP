"""Real SQL Server regression, opt-in through TEST_SQL_SERVER.

The disposable SQL Server uses a self-signed certificate. Verify default trust
rejection and explicit certificate-trust selection through the real driver.
"""
import os
import time
from contextlib import contextmanager
from decimal import Decimal

import pytest
from devlens_reporting.database import Database
from devlens_reporting.models import Catalog, Connection, Query
from devlens_reporting.query import compile_query

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_SQL_SERVER"), reason="No disposable SQL Server configured")


def test_real_read_only_sql(monkeypatch):
    import pyodbc
    server = os.environ["TEST_SQL_SERVER"]
    password = os.environ["TEST_SQL_SA_PASSWORD"]
    admin_dsn = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};UID=sa;PWD={password};Encrypt=yes;TrustServerCertificate=yes"
    connection = None
    for _ in range(60):
        try:
            connection = pyodbc.connect(admin_dsn, timeout=2, autocommit=True)
            break
        except pyodbc.Error:
            time.sleep(2)
    assert connection is not None, "Disposable SQL Server failed to start"
    reader_password = "Synthetic Reader#;{}-Password-123!a"
    try:
        connection.execute("CREATE DATABASE DevLensTest")
        connection.execute("USE DevLensTest")
        connection.execute("CREATE TABLE dbo.Sales(customer nvarchar(100), amount decimal(18,2))")
        connection.execute("INSERT dbo.Sales VALUES ('A',100.10),('A',200.20),('B',20.00)")
        connection.execute(f"CREATE LOGIN DevLensReader WITH PASSWORD='{reader_password}'")
        connection.execute("CREATE USER DevLensReader FOR LOGIN DevLensReader")
        connection.execute("GRANT SELECT ON dbo.Sales TO DevLensReader")
        connection.execute("DENY INSERT,UPDATE,DELETE ON dbo.Sales TO DevLensReader")
    finally:
        connection.close()
    monkeypatch.setenv("DEVLENS_SQL_PASSWORD", reader_password)
    host, port = server.split(",")
    settings = Connection(server=host, port=int(port), database="DevLensTest", username="DevLensReader",
                          trust_server_certificate=True)
    catalog = Catalog.model_validate({"version": "sql-test", "datasets": [{"id": "sales", "object_name": "Sales",
        "description": "Synthetic SQL fixture", "grain": "sale", "columns": [
            {"name": "customer", "description": "customer", "kind": "text"},
            {"name": "amount", "description": "amount", "kind": "number"}]}]})
    database = Database()
    with pytest.raises(pyodbc.Error):
        database.validate(settings.model_copy(update={"trust_server_certificate": False}), catalog)
    database.validate(settings, catalog)
    database.validate(settings.model_copy(update={"encrypt": False}), Catalog(version="optional-tls"))
    assert any(o["object"] == "Sales" for o in database.objects(settings))
    query = Query(dataset="sales", group_by=["customer"], measures=[{"column": "amount", "function": "sum", "alias": "total"}])
    sql, values = compile_query(catalog.datasets[0], query)
    rows, truncated = database.execute(settings, sql, values, 100)
    assert rows == [{"customer": "A", "total": "300.30"}, {"customer": "B", "total": "20.00"}]
    assert truncated is False
    with database.connect(settings) as reader:
        with pytest.raises(pyodbc.Error):
            reader.execute("INSERT dbo.Sales VALUES ('forbidden',1)")
