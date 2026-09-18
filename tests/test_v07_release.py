"""Legacy v0.7 remains importable; only test_v08_release pins the current package."""
from adaptive_harness.v07_cli import build
from adaptive_harness.v07_config import HarnessConfig


def test_v07_compatibility_entrypoints_remain_available():
    assert callable(build)
    assert callable(HarnessConfig.from_yaml)
