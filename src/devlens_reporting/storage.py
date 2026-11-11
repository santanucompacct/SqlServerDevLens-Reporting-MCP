"""Single-process atomic publication with immutable, separate JSON catalogs."""
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from .models import State


def atomic_write(path: Path, raw: bytes):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".publish-")
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.read()

    def read(self) -> tuple[State, str]:
        raw = self.path.read_bytes()
        value = json.loads(raw)
        # Inline catalogs remain supported for initial setup and older state files.
        if "catalog_file" in value:
            filename = value.pop("catalog_file")
            if not isinstance(filename, str) or not re.fullmatch(r"catalog-[a-f0-9]{64}\.json", filename):
                raise ValueError("Invalid published catalog reference")
            catalog_raw = (self.path.parent / filename).read_bytes()
            if hashlib.sha256(catalog_raw).hexdigest() != filename[8:-5]:
                raise ValueError("Published catalog content changed")
            value["catalog"] = json.loads(catalog_raw)
        return State.model_validate(value), hashlib.sha256(raw).hexdigest()

    def publish(self, state: State, revision: str) -> str:
        with self.lock:
            if self.read()[1] != revision:
                raise ValueError("Configuration changed; reload before publishing")
            catalog_raw = json.dumps(state.catalog.model_dump(), indent=2).encode()
            filename = "catalog-" + hashlib.sha256(catalog_raw).hexdigest() + ".json"
            # Publish the immutable catalog before its manifest. Interrupted writes
            # leave the previous manifest readable, with at most an unused catalog.
            atomic_write(self.path.parent / filename, catalog_raw)
            value = state.model_dump(exclude={"catalog"})
            value["catalog_file"] = filename
            raw = json.dumps(value, indent=2).encode()
            atomic_write(self.path, raw)
            return hashlib.sha256(raw).hexdigest()
