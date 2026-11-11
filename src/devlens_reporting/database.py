"""SQL connections use fixed driver/TLS settings; callers supply no SQL."""
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from .models import Connection, Catalog


def odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


class RedactedDatabaseError(Exception):
    """Provider diagnostic with the password used for this connection removed."""
    def __init__(self, error: Exception, password: str, settings: Connection):
        values = [str(value) for value in getattr(error, "args", ())]
        message = " | ".join(values)
        variants = {password, odbc_value(password), password.replace("}", "}}"),
                    password.replace("'", "''"), password.encode("unicode_escape").decode("ascii")}
        variants.update({settings.server, settings.database, settings.username})
        for secret in sorted(variants, key=len, reverse=True):
            if secret:
                message = message.replace(secret, "[redacted]")
        message = re.sub(r"(?i)\b(?:PWD|PASSWORD)\s*=\s*(?:\{(?:[^}]|}})*\}|[^;\r\n]*)",
                         "PWD=[redacted]", message)
        self.safe_provider_detail = message[:2000]
        self.provider_error_type = getattr(error, "provider_error_type", type(error).__name__)
        state = values[0] if values and re.fullmatch(r"[A-Z0-9]{5}", values[0]) else "HY000"
        super().__init__(state, self.safe_provider_detail)


class Database:
    def __init__(self, demo: bool = False, password_path: Path | None = None):
        self.demo = demo
        self.password_path = password_path

    @contextmanager
    def connect(self, settings: Connection):
        if self.demo:
            connection = sqlite3.connect(":memory:")
            connection.executescript("""
                ATTACH DATABASE ':memory:' AS dbo;
                CREATE TABLE dbo.Sales (customer TEXT, doc_date TEXT, amount REAL, branch TEXT);
                INSERT INTO dbo.Sales VALUES
                ('Example A','2026-01-01',100,'East'),
                ('Example B','2026-01-02',250,'West'),
                ('Example A','2026-02-01',150,'East');
            """)
        else:
            import pyodbc
            # Each reporting operation owns and closes its physical connection.
            pyodbc.pooling = False
            if self.password_path is not None and self.password_path.exists():
                password = self.password_path.read_bytes().decode("utf-8")
            else:
                password = os.environ.get(settings.password_env)
            if not all((settings.server, settings.database, settings.username, password)):
                raise ValueError("SQL settings or saved password are missing")
            server = f"tcp:{settings.server},{settings.port}"
            connection = None
            try:
                connection = pyodbc.connect(
                    "DRIVER={ODBC Driver 18 for SQL Server};"
                    f"SERVER={odbc_value(server)};DATABASE={odbc_value(settings.database)};"
                    f"UID={odbc_value(settings.username)};PWD={odbc_value(password)};"
                    f"Encrypt={'yes' if settings.encrypt else 'no'};"
                    f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'};"
                    "ApplicationIntent=ReadOnly;"
                    "APP=DevLensReporting;", timeout=10, autocommit=True)
                connection.timeout = settings.query_timeout_seconds
            except pyodbc.Error as error:
                if connection is not None:
                    connection.close()
                raise RedactedDatabaseError(error, password, settings) from None
        try:
            try:
                yield connection
            finally:
                connection.close()
        except Exception as error:
            if self.demo or not isinstance(error, pyodbc.Error):
                raise
            raise RedactedDatabaseError(error, password, settings) from None

    def validate(self, settings: Connection, catalog: Catalog):
        from .query import quote
        with self.connect(settings) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
                for dataset in catalog.datasets:
                    fields = ", ".join(quote(c.name) for c in dataset.columns)
                    cursor.execute(f"SELECT {fields} FROM {quote(dataset.schema_name)}.{quote(dataset.object_name)} WHERE 1=0")
                    cursor.fetchall()

    def objects(self, settings: Connection):
        if self.demo:
            return [{"schema": "dbo", "object": "Sales", "type": "demo"}]
        with self.connect(settings) as connection:
            with closing(connection.cursor()) as cursor:
                rows = cursor.execute("""
                    SELECT TOP (500) s.name, o.name, o.type_desc
                    FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
                    WHERE o.type IN ('U','V') AND o.is_ms_shipped=0
                    ORDER BY s.name,o.name
                """).fetchall()
                return [{"schema": r[0], "object": r[1], "type": r[2]} for r in rows]

    def columns(self, settings: Connection, schema: str, name: str):
        if self.demo:
            return [{"name": n, "sql_type": t, "nullable": True} for n, t in
                    [("customer", "nvarchar"), ("doc_date", "date"), ("amount", "decimal"), ("branch", "nvarchar")]]
        with self.connect(settings) as connection:
            with closing(connection.cursor()) as cursor:
                rows = cursor.execute("""
                    SELECT c.name,t.name,c.is_nullable
                    FROM sys.columns c JOIN sys.objects o ON o.object_id=c.object_id
                    JOIN sys.schemas s ON s.schema_id=o.schema_id
                    JOIN sys.types t ON t.user_type_id=c.user_type_id
                    WHERE s.name=? AND o.name=? AND o.type IN ('U','V')
                    ORDER BY c.column_id
                """, schema, name).fetchall()
                return [{"name": r[0], "sql_type": r[1], "nullable": bool(r[2])} for r in rows]

    def execute(self, settings: Connection, sql: str, values: list, limit: int):
        if self.demo:
            top = re.match(r"SELECT TOP \((\d+)\) ", sql)
            sql = sql.replace(top.group(0), "SELECT ", 1).replace("COUNT_BIG(", "COUNT(")
            sql += " LIMIT " + top.group(1)
        with self.connect(settings) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(sql, values)
                names = [description[0] for description in cursor.description]
                rows, size = [], 0
                while len(rows) < limit + 1:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    result = {}
                    for name, value in zip(names, row):
                        if isinstance(value, (date, datetime)):
                            value = value.isoformat()
                        elif isinstance(value, Decimal):
                            value = str(value)  # Preserve exact monetary precision.
                        elif isinstance(value, bytes):
                            raise ValueError("Binary columns are unsupported")
                        if isinstance(value, str) and len(value) > 16000:
                            raise ValueError("Result contains an oversized cell; narrow the query")
                        result[name] = value
                    size += len(str(result).encode())
                    if size > 2_000_000:
                        raise ValueError("Result exceeds 2 MB; narrow the query")
                    rows.append(result)
                return rows[:limit], len(rows) > limit



def connection_error_message(error: Exception, stage: str = "connection") -> str:
    """Classify provider errors without returning hostnames, credentials or raw messages."""
    if isinstance(error, ValueError):
        return "Enter the SQL server, database and username, and save the SQL password."
    arguments = getattr(error, "args", ())
    details = " ".join(str(value) for value in arguments)
    lowered = details.lower()
    state = str(arguments[0]) if arguments else ""
    codes = set(re.findall(r"\((\d+)\)", details))
    if "18456" in codes or state == "28000":
        return "SQL login failed. Verify the username, password and SQL authentication access (18456/28000)."
    if "4060" in codes:
        return "The selected database could not be opened. Verify its name and login access (4060)."
    if codes & {"229", "916"}:
        return "SQL permission denied. Grant the reporting user access to the database and SELECT on the intended view."
    certificate_errors = ("certificate verify failed", "certificate chain", "self-signed certificate",
                          "hostname mismatch", "certificate subject name")
    if any(marker in lowered for marker in certificate_errors):
        return ("SQL TLS certificate validation failed. Check the SQL hostname and trusted CA, "
                "or explicitly select Trust server certificate for a controlled connection test.")
    if "ssl provider" in lowered or "tls" in lowered:
        return "SQL TLS negotiation failed. Check SQL Server TLS support and the selected encryption settings."
    if state in {"HYT00", "HYT01"} or "timeout" in lowered or "timed out" in lowered:
        if stage == "query":
            return "SQL query exceeded its execution time limit. Narrow the date range or ask the administrator to review view performance and blocking."
        return "SQL connection timed out. Check the hostname, TCP port, firewall and server availability."
    if state in {"08001", "08S01"}:
        return "SQL Server could not be reached. Check the hostname, TCP port and network access from the reporting VM."
    if state == "IM002":
        return "Microsoft ODBC Driver 18 for SQL Server is unavailable on the reporting host."
    if stage in {"metadata", "query"}:
        error_type = type(error).__name__
        allowed_types = {"Exception", "Error", "ProgrammingError", "OperationalError", "InterfaceError",
                         "DatabaseError", "DataError", "NotSupportedError", "TypeError", "AttributeError",
                         "RuntimeError", "PermissionError", "UnicodeDecodeError", "IndexError", "KeyError"}
        if error_type not in allowed_types:
            error_type = "Error"
        sqlstate = state if re.fullmatch(r"[A-Z0-9]{5}", state) else "unavailable"
        summary = "SQL query failed. " if stage == "query" else "SQL connection opened, but metadata inspection failed. "
        message = summary + f"Diagnostic {error_type} / SQLSTATE {sqlstate}."
        if isinstance(error, RedactedDatabaseError):
            message += " Driver message " + error.safe_provider_detail
        return message
    return "SQL connection failed. Check authentication, database access, network connectivity and TLS settings."
