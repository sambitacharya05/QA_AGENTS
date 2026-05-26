"""
Local JSON persistence helper providing:
  - Atomic writes via tempfile + os.replace (POSIX/Windows safe)
  - Cross-platform advisory file locking (fcntl on POSIX, msvcrt on Windows)
  - .bak backup of last-good copy after every successful write
  - .corrupt quarantine when a file cannot be parsed and no backup rescue succeeds
  - Schema-version envelope with automatic migration dispatch
  - Optional raw mode (no envelope) for stores whose tests read the raw file directly

Usage:
    store = LocalJsonStore(path, schema_version=1, migrators={0: fn})
    with store.lock():
        payload = store.safe_read() or {}
        payload["key"] = "value"
        store.atomic_write(payload)

    # Or for batch read-modify-write without managing the lock manually:
    # Use GraphStore.transaction() which wraps this.
"""

import os
import json
import time
import shutil
import tempfile
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

try:
    import fcntl
    _LOCK_BACKEND = "fcntl"
except ImportError:  # Windows
    import msvcrt  # type: ignore[import]
    _LOCK_BACKEND = "msvcrt"

log = logging.getLogger(__name__)

SCHEMA_GENERATOR = "context_builder"


class LocalStoreCorruptError(RuntimeError):
    """Raised when the on-disk JSON cannot be parsed AND no .bak rescue succeeded."""


class LocalJsonStore:
    """Single-writer-safe persistence helper for local JSON files.

    Parameters
    ----------
    file_path:
        Absolute path to the target JSON file.
    schema_version:
        Current schema version understood by this code.  When *use_envelope* is
        True (default), every write wraps the payload in::

            {"schema_version": N, "generator": "context_builder",
             "written_at": "<iso8601>", "payload": <data>}

        Legacy files without the envelope are treated as version 0 and migrated
        forward using *migrators*.
    migrators:
        Dict mapping ``from_version → callable(payload) → payload``.
        The callable receives the raw payload (not the envelope) and returns the
        payload upgraded to the next version.
    use_envelope:
        Set to *False* to skip schema-version wrapping entirely.  Writes store
        plain JSON; reads return plain JSON.  Use this for stores whose on-disk
        format is tested by external tools/tests that read the raw file.
    """

    def __init__(
        self,
        file_path: str,
        schema_version: int = 1,
        migrators: Optional[Dict[int, Callable[[dict], dict]]] = None,
        *,
        use_envelope: bool = True,
    ) -> None:
        self.file_path = file_path
        self.lock_path = f"{file_path}.lock"
        self.bak_path = f"{file_path}.bak"
        self.tmp_dir = os.path.dirname(file_path) or "."
        self.schema_version = schema_version
        self.migrators: Dict[int, Callable[[dict], dict]] = migrators or {}
        self.use_envelope = use_envelope

    # ------------------------------------------------------------------ locking

    @contextmanager
    def lock(self, timeout: float = 10.0):
        """Advisory exclusive lock over *lock_path*.  Blocks up to *timeout* seconds."""
        os.makedirs(self.tmp_dir, exist_ok=True)
        fp = open(self.lock_path, "w")
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    if _LOCK_BACKEND == "fcntl":
                        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[name-defined]
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Could not acquire lock on {self.lock_path} within {timeout}s"
                        )
                    time.sleep(0.05)
            yield
        finally:
            try:
                if _LOCK_BACKEND == "fcntl":
                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                else:
                    msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[name-defined]
            except Exception:
                pass
            fp.close()

    # ------------------------------------------------------------------- read

    def safe_read(self) -> Optional[Dict[str, Any]]:
        """Read and return the payload, trying the primary file then the backup.

        Returns *None* if neither file exists.
        Raises *LocalStoreCorruptError* if both files are unreadable/unparseable.
        """
        last_error: Optional[Exception] = None
        for path in (self.file_path, self.bak_path):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not read %s: %s", path, exc)
                last_error = exc
                if path == self.file_path:
                    self._quarantine(path)
                continue

            if not self.use_envelope:
                return raw  # raw mode — no envelope processing

            return self._migrate_in(raw, source=path)

        # Both paths failed or neither existed
        if last_error is not None:
            raise LocalStoreCorruptError(
                f"All copies of {self.file_path} are unreadable and no backup "
                f"could be rescued. Last error: {last_error}"
            )
        return None  # File simply doesn't exist yet

    def _quarantine(self, path: str) -> None:
        quarantine = f"{path}.corrupt.{int(time.time())}"
        try:
            shutil.copy2(path, quarantine)
            log.warning("Quarantined corrupt store to %s", quarantine)
        except Exception as exc:
            log.warning("Could not quarantine %s: %s", path, exc)

    def _migrate_in(self, raw: dict, source: str) -> Dict[str, Any]:
        """Unwrap the schema envelope and migrate the payload to the current version."""
        if not isinstance(raw, dict) or "schema_version" not in raw:
            # Legacy file — no envelope; assume version 0
            payload = raw
            version = 0
        else:
            payload = raw.get("payload", {})
            version = int(raw.get("schema_version", 0))

        while version < self.schema_version:
            migrator = self.migrators.get(version)
            if migrator is None:
                raise LocalStoreCorruptError(
                    f"No migrator from schema v{version} to v{version + 1} "
                    f"registered for {source}.  Cannot open this file."
                )
            log.info("Migrating %s: schema v%d → v%d", source, version, version + 1)
            payload = migrator(payload)
            version += 1

        return payload

    # ------------------------------------------------------------------ write

    def atomic_write(self, payload: Dict[str, Any]) -> None:
        """Atomically persist *payload* to disk.

        Steps:
        1. Write to a temp file in the same directory (same filesystem → rename is atomic).
        2. fsync the temp file.
        3. Rotate the existing file to *.bak*.
        4. os.replace(tmp → target)  — atomic on POSIX and Windows.
        """
        if self.use_envelope:
            data_to_write: Dict[str, Any] = {
                "schema_version": self.schema_version,
                "generator": SCHEMA_GENERATOR,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
        else:
            data_to_write = payload

        os.makedirs(self.tmp_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".write-", suffix=".json", dir=self.tmp_dir
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data_to_write, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Rotate old → .bak before replacing
            if os.path.exists(self.file_path):
                try:
                    shutil.copy2(self.file_path, self.bak_path)
                except Exception as exc:
                    log.warning(
                        "Could not rotate backup for %s: %s", self.file_path, exc
                    )

            os.replace(tmp_path, self.file_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
