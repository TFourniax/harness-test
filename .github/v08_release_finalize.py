from pathlib import Path

path = Path("tests/test_v07_release.py")
path.write_text(
    '''from adaptive_harness import v07_cli\n\n\ndef test_v07_runtime_builder_remains_available_as_compatibility_baseline():\n    # v0.8 intentionally reuses v0.7 state/cell/lease/escrow machinery. Keeping the old builder\n    # importable gives benchmarks and rollback tests a stable single-leaf baseline without pinning\n    # the package's current release identity to 0.7.\n    assert callable(v07_cli.build)\n    assert v07_cli.app is not None\n''',
    encoding="utf-8",
)
print("Converted v0.7 release pin into a v0.7 compatibility invariant")
