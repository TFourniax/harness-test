from adaptive_harness.tools.evidence_tools import classify_verification_command


def test_verification_command_allowlist_rejects_arbitrary_shell_work():
    assert classify_verification_command(["python", "-m", "pytest", "-q"]) == "tests"
    assert classify_verification_command(["ruff", "check", "src"]) == "lint"
    assert classify_verification_command(["npm", "run", "build"]) == "build"
    assert classify_verification_command(["echo", "looks-good"]) is None
    assert classify_verification_command(["bash", "-lc", "pytest -q && rm -rf x"]) is None
