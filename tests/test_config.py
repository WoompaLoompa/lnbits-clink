import json
import pathlib

import clink
from clink import migrations

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_config_json_valid():
    config = json.loads((REPO_ROOT / "config.json").read_text())
    assert config["id"] == "clink"
    assert config["name"] == "CLINK"
    assert config["license"] == "GPL-3.0"
    assert config["min_lnbits_version"]
    assert config["tile"] == "/clink/static/clink.png"


def test_extension_exports():
    assert callable(clink.clink_start)
    assert callable(clink.clink_stop)
    assert isinstance(clink.clink_static_files, list)
    assert clink.clink_ext is not None


def test_db_service_name():
    assert clink.db.name == "ext_clink"


def test_migrations_present():
    assert hasattr(migrations, "m001_initial")
