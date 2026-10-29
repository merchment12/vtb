import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# до любого импорта app: settings читаются один раз, иначе тесты пишут в рабочую unblock.db и uploads/
_TMP = Path(tempfile.mkdtemp(prefix="vtb-unblock-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["BANK_CORE_DIR"] = str(_TMP / "bank_core")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def samples():
    import json
    import subprocess

    if not (ROOT / "samples" / "README.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_samples.py")], check=True)
    return json.loads((ROOT / "samples" / "README.json").read_text("utf-8"))
