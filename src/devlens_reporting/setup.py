"""Private setup drafts, independent of the participant-facing published catalog."""
import hashlib
import json
import threading
from pathlib import Path
from .models import Connection
from .storage import atomic_write


def connection_key(connection: Connection) -> str:
    identity = connection.model_dump(include={"server", "port", "database", "username"})
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def object_key(connection: Connection, schema: str, name: str) -> str:
    return hashlib.sha256(json.dumps([connection_key(connection), schema, name]).encode()).hexdigest()


class SetupStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def read(self):
        raw = self.path.read_bytes() if self.path.exists() else b'{}'
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def update(self, revision: str, change):
        with self.lock:
            value, current = self.read()
            if current != revision:
                raise ValueError("Saved setup changed; load the configuration again before saving")
            change(value)
            raw = json.dumps(value, indent=2).encode()
            atomic_write(self.path, raw)
            return hashlib.sha256(raw).hexdigest()

    def save_connection(self, connection: Connection, revision: str):
        return self.update(revision, lambda value: value.update(connection=connection.model_dump()))

    def save_object(self, connection: Connection, schema: str, name: str, columns: list, revision: str):
        def change(value):
            value.setdefault("objects", {})[object_key(connection, schema, name)] = {
                "connection_key": connection_key(connection), "schema": schema,
                "object": name, "columns": columns}
        return self.update(revision, change)

    def columns(self, connection: Connection, schema: str, name: str, current: list):
        value, revision = self.read()
        saved = value.get("objects", {}).get(object_key(connection, schema, name), {})
        remarks = {column["name"]: column.get("remarks", "") for column in saved.get("columns", [])}
        return [{**column, "remarks": remarks.get(column["name"], "")} for column in current], revision

    def configuration(self, fallback: Connection):
        value, revision = self.read()
        connection = Connection.model_validate(value.get("connection", fallback.model_dump()))
        objects = [{"schema": item["schema"], "object": item["object"], "type": "saved"}
                   for item in value.get("objects", {}).values()
                   if item["connection_key"] == connection_key(connection)]
        return {"connection": connection.model_dump(), "revision": revision,
                "saved": "connection" in value, "objects": objects}
