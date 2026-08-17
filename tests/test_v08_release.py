from pathlib import Path
import tomllib

import adaptive_harness


def test_v08_package_version_and_console_entrypoint_are_pinned():
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["version"] == "0.8.0"
    assert project["scripts"]["adaptive-harness"] == "adaptive_harness.v08_cli:app"
    assert adaptive_harness.__version__ == "0.8.0"
