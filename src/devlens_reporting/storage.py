"""Single-process atomic published configuration with optimistic revision checks."""
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from .models import State


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.read()

    def read(self) -> tuple[State, str]:
        raw = self.path.read_bytes()
        return State.model_validate_json(raw), hashlib.sha256(raw).hexdigest()

    def publish(self, state: State, revision: str) -> str:
        with self.lock:
            if self.read()[1] != revision:
                raise ValueError("Configuration changed; reload before publishing")
            raw = json.dumps(state.model_dump(), indent=2).encode()
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".publish-")
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return hashlib.sha256(raw).hexdigest()
