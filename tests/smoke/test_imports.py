"""Every top-level package the env was asked for can be imported (or its command runs).

The package list comes from the env itself (`--spec`, which harness/run.sh generates
with `conda env export --from-history`), so this works for any NPL version.
Each check runs in a fresh interpreter so one crashing package can't hide the rest.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.smoke

RULES = yaml.safe_load((Path(__file__).parents[1] / "data" / "packages.yml").read_text())


def spec_packages(path):
    names, in_deps = [], False
    for line in Path(path).read_text().splitlines():
        if line.startswith("dependencies:"):
            in_deps = True
            continue
        if in_deps:
            m = re.match(r"^\s+- ([A-Za-z0-9_.\-]+)", line)
            if m:
                names.append(m.group(1))
            elif line.strip() and not line.startswith(" "):
                break
    return sorted(set(names) - set(RULES["skip"]))


def pytest_generate_tests(metafunc):
    if "package" in metafunc.fixturenames:
        spec = metafunc.config.getoption("--spec")
        if spec:
            names = spec_packages(spec)
            # an empty list would silently turn the whole smoke suite into skips
            metafunc.parametrize("package", names or [None])
        else:
            metafunc.parametrize("package", [pytest.param(None, marks=pytest.mark.skip(
                reason="no --spec given"))])


def _last_line(proc):
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return lines[-1] if lines else f"exit {proc.returncode}"


@pytest.mark.timeout(300)
def test_package_usable(package, request):
    if package is None:
        pytest.fail(f"no packages found in {request.config.getoption('--spec')}", pytrace=False)
    if package in RULES["commands"]:
        cmd = RULES["commands"][package]
        if shutil.which(cmd[0]) is None:
            pytest.fail(f"command {cmd[0]!r} not on PATH", pytrace=False)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        assert proc.returncode == 0, _last_line(proc)
        return
    modules = RULES["import_names"].get(package, package.replace("-", "_"))
    for module in ([modules] if isinstance(modules, str) else modules):
        proc = subprocess.run([sys.executable, "-c", f"import {module}"],
                              capture_output=True, text=True, timeout=240)
        if proc.returncode == 0:
            return
    assert proc.returncode == 0, _last_line(proc)
