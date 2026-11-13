"""A kernel made the way users make it (bin/create_conda_kernel) starts and works.

The kernel's launcher does `module load ncarenv conda` + `conda activate` in a non-login
shell, as on JupyterHub. The kernel is started from a *stripped* environment (no conda
activation inherited from the test process), so these tests check that the launcher itself
activates the env: right python, the env's variables, activation-script variables.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.jupyter
jupyter_client = pytest.importorskip("jupyter_client")

PREFIX = Path(sys.prefix).resolve()
KERNEL_CREATOR = Path(os.environ.get(
    "NPL_KERNEL_CREATOR", Path(__file__).resolve().parents[2] / "bin" / "create_conda_kernel"))
# variables set by conda activation / activate.d scripts, removed before starting the kernel
ACTIVATION_PREFIXES = ("CONDA_", "_CE_", "PROJ_", "GDAL_", "ESMF", "UDUNITS2_", "NPL_",
                       "PYTHONPATH", "MPLBACKEND", "UCX_", "FI_")


def unactivated_env(extra_names, jupyter_path):
    """os.environ minus everything conda activation added: what a fresh login shell has."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(ACTIVATION_PREFIXES) and k not in extra_names}
    env["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                  if p and not os.path.realpath(p).startswith(str(PREFIX))
                                  and "condabin" not in p)
    env["JUPYTER_PATH"] = str(jupyter_path)
    return env


@pytest.fixture(scope="module")
def kernel(tmp_path_factory, declared_env_vars):
    """Create a kernelspec with create_conda_kernel in a private JUPYTER_PATH, start it."""
    if not KERNEL_CREATOR.exists():
        pytest.skip(f"kernel creator not found at {KERNEL_CREATOR}")
    conda_sh = os.environ.get("NPL_CONDA_SH")
    if not conda_sh:
        pytest.skip("NPL_CONDA_SH not set (run through harness/run.sh)")
    jupyter_path = tmp_path_factory.mktemp("jupyter")
    name = f"npltest-{uuid.uuid4().hex[:8]}"
    kernel_dir = jupyter_path / "kernels" / name
    env = unactivated_env(declared_env_vars, jupyter_path)

    # Like a user's interactive shell: conda's shell function available, then the creator
    proc = subprocess.run(
        ["bash", "-c", f'source "{conda_sh}" && "{KERNEL_CREATOR}" -n {name} -p "{kernel_dir}" "{PREFIX}"'],
        input="y\n", capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0 and (kernel_dir / "kernel.json").exists(), \
        f"create_conda_kernel failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    spec = json.loads((kernel_dir / "kernel.json").read_text())

    ksm = jupyter_client.kernelspec.KernelSpecManager(kernel_dirs=[str(jupyter_path / "kernels")])
    km = jupyter_client.KernelManager(kernel_name=name, kernel_spec_manager=ksm)
    km.start_kernel(env=env)
    kc = km.client()
    kc.start_channels()
    try:
        kc.wait_for_ready(timeout=180)
        yield kc, km, spec
    finally:
        kc.stop_channels()
        km.shutdown_kernel(now=True)


def run_cell(kernel, code, timeout=180):
    """Execute code with an overall deadline; interrupt the kernel if it is exceeded.
    Returns (reply content, list of (msg_type, content) iopub messages)."""
    kc, km, _ = kernel
    deadline = time.monotonic() + timeout
    msg_id = kc.execute(code)
    outputs = []
    try:
        while True:
            msg = kc.get_iopub_msg(timeout=max(1, deadline - time.monotonic()))
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            if msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle":
                break
            outputs.append((msg["msg_type"], msg["content"]))
            if time.monotonic() > deadline:
                raise TimeoutError
        while True:
            reply = kc.get_shell_msg(timeout=max(1, deadline - time.monotonic()))
            if reply["parent_header"].get("msg_id") == msg_id:
                return reply["content"], outputs
    except Exception:
        km.interrupt_kernel()  # leave the kernel usable for the next test
        pytest.fail(f"cell did not finish within {timeout}s:\n{code}", pytrace=False)


def stdout_of(outputs):
    return "".join(c["text"] for t, c in outputs if t == "stream" and c["name"] == "stdout")


def assert_ok(reply, outputs):
    errors = [c for t, c in outputs if t == "error"]
    assert reply["status"] == "ok", errors[0]["traceback"][-1] if errors else reply


def test_kernelspec_uses_launcher(kernel):
    spec = kernel[2]
    assert spec["argv"][0].endswith("/launcher"), spec["argv"]


@pytest.mark.timeout(240)
def test_kernel_runs_env_python(kernel):
    reply, out = run_cell(kernel, "import sys, os; print(os.path.realpath(sys.prefix))")
    assert_ok(reply, out)
    assert stdout_of(out).strip() == str(PREFIX)


@pytest.mark.timeout(240)
def test_kernel_has_activated_environment(kernel, declared_env_vars):
    names = sorted(declared_env_vars) + ["PROJ_DATA", "CONDA_PREFIX"]
    reply, out = run_cell(kernel, "import os, json; print(json.dumps({k: os.environ.get(k) for k in %r}))" % names)
    assert_ok(reply, out)
    seen = json.loads(stdout_of(out))
    for key, value in declared_env_vars.items():
        assert seen[key] == value, f"{key} not set by activation in the kernel"
    assert seen["CONDA_PREFIX"] and os.path.realpath(seen["CONDA_PREFIX"]) == str(PREFIX), \
        "conda activate did not run in the kernel launcher"
    if (PREFIX / "share" / "proj").is_dir():
        assert seen["PROJ_DATA"] and str(PREFIX) in os.path.realpath(seen["PROJ_DATA"]), \
            "activation scripts (activate.d) did not run in the kernel"


@pytest.mark.timeout(240)
def test_inline_matplotlib_figure(kernel):
    pytest.importorskip("matplotlib")
    reply, out = run_cell(kernel, "%matplotlib inline\nimport matplotlib.pyplot as plt\n"
                                  "plt.plot([1, 2, 3]); plt.show()")
    assert_ok(reply, out)
    assert any(t == "display_data" and "image/png" in c["data"] for t, c in out), \
        "no PNG figure was displayed"


@pytest.mark.timeout(240)
def test_ipywidgets_open_a_comm(kernel):
    pytest.importorskip("ipywidgets")
    reply, out = run_cell(kernel, "import ipywidgets as w\nfrom IPython.display import display\n"
                                  "display(w.IntSlider(value=5))")
    assert_ok(reply, out)
    assert any(t == "comm_open" for t, _ in out), "widget did not open a comm"
    assert any(t == "display_data" and "application/vnd.jupyter.widget-view+json" in c["data"]
               for t, c in out), "no widget view was displayed"


@pytest.mark.timeout(240)
def test_ipympl_widget_backend(kernel):
    pytest.importorskip("ipympl")
    reply, out = run_cell(kernel, "%matplotlib widget\nimport matplotlib.pyplot as plt\n"
                                  "fig = plt.figure(); plt.plot([1, 2]); display(fig.canvas)")
    assert_ok(reply, out)
    assert any(t == "comm_open" for t, _ in out), "ipympl canvas did not open a widget comm"


@pytest.mark.timeout(300)
def test_dask_cluster_inside_kernel(kernel):
    pytest.importorskip("distributed")
    code = ("from distributed import Client, LocalCluster\n"
            "with LocalCluster(n_workers=1, threads_per_worker=1, processes=False, "
            "dashboard_address=':0') as c, Client(c) as cl:\n"
            "    print(cl.submit(sum, [1, 2, 3]).result(timeout=60))")
    reply, out = run_cell(kernel, code, timeout=240)
    assert_ok(reply, out)
    assert stdout_of(out).strip().endswith("6")
