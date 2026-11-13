"""Run the browser-script behaviour tests (tests/frontend/*.test.mjs) under pytest.

The tests themselves are Node built-in test-runner files driven by
tests/frontend/harness.mjs. This wrapper only makes `pytest` include them.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def test_frontend_behaviour_suite_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", "tests/frontend/*.test.mjs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
