"""
Hermetic test harness for Jev v8.

Guarantees that NO test can read from or write to the committed production
`verdicts.db`:
  1. JEV_VERDICTS_DB_PATH is set at conftest import time (before any `app.*` import),
     so the module-level `verdicts_db` singleton is born pointing at a throwaway file.
  2. An autouse fixture re-points the singleton at a fresh per-test SQLite file, so
     tests are independent of each other and of execution order.
  3. A session guard fingerprints the real verdicts.db (+ WAL/SHM sidecars) before the
     run and fails the session if anything changed.
"""
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
PRODUCTION_DB = ROOT_DIR / "verdicts.db"
PRODUCTION_SIDECARS = [PRODUCTION_DB, ROOT_DIR / "verdicts.db-wal", ROOT_DIR / "verdicts.db-shm"]

# (1) Redirect before any app module is imported by test modules.
_SESSION_TMP_DIR = Path(tempfile.mkdtemp(prefix="jev_v8_tests_"))
os.environ["JEV_VERDICTS_DB_PATH"] = str(_SESSION_TMP_DIR / "bootstrap_verdicts.db")


def _fingerprint() -> dict:
    fp = {}
    for p in PRODUCTION_SIDECARS:
        fp[p.name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return fp


@pytest.fixture(scope="session", autouse=True)
def production_db_untouched_guard():
    """(3) Fail the session if the committed verdicts.db or its sidecars were mutated."""
    before = _fingerprint()
    yield
    after = _fingerprint()
    assert before == after, (
        "Hermeticity violation: the committed production verdicts.db (or WAL/SHM) was "
        f"modified during the test session. before={before} after={after}"
    )


@pytest.fixture(autouse=True)
def isolated_verdicts_db(tmp_path, monkeypatch):
    """(2) Point the shared verdicts_db singleton at a fresh per-test database."""
    from app.core.verdicts_db import verdicts_db

    test_db_path = tmp_path / "verdicts_test.db"
    monkeypatch.setenv("JEV_VERDICTS_DB_PATH", str(test_db_path))
    monkeypatch.setattr(verdicts_db, "db_path", test_db_path)
    verdicts_db.init_db()

    assert verdicts_db.db_path.resolve() != PRODUCTION_DB.resolve()
    yield verdicts_db
