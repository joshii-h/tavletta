"""The web page's JavaScript logic (number ranges, row lists, sample values,
the assignment of columns, times and locales) with Node's
own test runner (tests/js/*.test.mjs): no package, no build step. Without
Node the tests are skipped, except in the CI and the package builds (CI set:
they install nodejs)."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs node on the repository's own tests
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_the_javascript_tests_pass():
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is needed for the web page's tests (CI set: install nodejs)")
        pytest.skip("node is not installed")
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert tests
    result = subprocess.run(  # nosec B603
        [node, "--test", *tests], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
