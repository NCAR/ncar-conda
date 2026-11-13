"""Shared setup for the NPL test suite.

Run through harness/run.sh, which does a real `conda activate` of the env under test and
then runs pytest with that env's own python. Nothing here assumes package versions:
tests skip when their package is absent, and capability-gated tests (gpu, mpi, network,
pbs) skip with a reason on nodes that can't run them.
"""
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PREFIX = Path(sys.prefix).resolve()

FINGERPRINT_VARS = (
    "NCAR_HOST", "PBS_JOBID", "CONDA_PREFIX", "LOADEDMODULES", "PROJ_DATA", "GDAL_DATA",
    "ESMFMKFILE", "MPLBACKEND",
)
FINGERPRINT_PREFIXES = ("UCX_", "FI_", "MPICH_", "HDF5_")


def pytest_addoption(parser):
    parser.addoption("--spec", help="environment.yml listing the env's top-level packages")
    parser.addoption("--run-pbs", action="store_true", help="allow tests that submit PBS jobs")
    parser.addoption("--account", default=os.environ.get("NPL_TEST_ACCOUNT"),
                     help="PBS account for --run-pbs tests")


# hosts the `network` tests actually download from (cartopy's Natural Earth data)
NETWORK_PROBE = ("naturalearth.s3.amazonaws.com", 443)


def _internet():
    try:
        socket.create_connection(NETWORK_PROBE, timeout=3).close()
        return True
    except OSError:
        return False


def _gpu():
    if shutil.which("nvidia-smi") is None:
        return False
    return subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0


def _cpu_model():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def pytest_configure(config):
    config.npl_caps = {
        "internet": _internet(),
        "gpu": _gpu(),
        "pbs_allocation": bool(os.environ.get("PBS_NODEFILE")),
    }


def pytest_report_header(config):
    c = config.npl_caps
    return [f"npl env: {PREFIX}  python {platform.python_version()}  host {socket.gethostname()}",
            f"capabilities: internet={c['internet']} gpu={c['gpu']} pbs_allocation={c['pbs_allocation']}"]


def pytest_sessionstart(session):
    """Record the conditions of the run next to the results."""
    out = os.environ.get("NPL_TEST_OUT")
    if not out:
        return
    env = {k: v for k, v in os.environ.items()
           if k in FINGERPRINT_VARS or k.startswith(FINGERPRINT_PREFIXES)}
    fp = {
        "prefix": str(PREFIX),
        "python": platform.python_version(),
        "host": socket.gethostname(),
        "cpu": _cpu_model(),
        "capabilities": session.config.npl_caps,
        "env_vars_declared": env_vars_declared(),
        "environment": env,
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "fingerprint.json").write_text(json.dumps(fp, indent=2))


def pytest_collection_modifyitems(config, items):
    caps = config.npl_caps
    gates = {
        "network": (not caps["internet"], "no outbound internet on this node"),
        "gpu": (not caps["gpu"], "no NVIDIA GPU on this node"),
        "mpi": (not caps["pbs_allocation"], "needs a PBS allocation (PBS_NODEFILE)"),
        "pbs": (not config.getoption("--run-pbs"), "submits jobs; opt in with --run-pbs"),
    }
    for item in items:
        for marker, (skip, reason) in gates.items():
            if skip and marker in item.keywords:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def _time_limit(request):
    """Fail (not hang) when a test exceeds its time limit; default 300 s."""
    m = request.node.get_closest_marker("timeout")
    seconds = int(m.args[0]) if m else 300

    def expired(signum, frame):
        pytest.fail(f"exceeded time limit of {seconds}s", pytrace=False)

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Run the test from its own temp dir (some libraries, e.g. ESMF, write logs to cwd)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def env_vars_declared():
    """What activation must set: conda's `variables:` (conda-meta/state) plus the NPL
    etc/npl/env_vars file (KEY=VALUE lines, applied by activate.d/deactivate.d scripts)."""
    state = PREFIX / "conda-meta" / "state"
    found = json.loads(state.read_text()).get("env_vars", {}) if state.exists() else {}
    npl = PREFIX / "etc" / "npl" / "env_vars"
    if npl.exists():
        for line in npl.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.isidentifier():
                found[key] = value
    return found


@pytest.fixture(scope="session")
def declared_env_vars():
    return env_vars_declared()
