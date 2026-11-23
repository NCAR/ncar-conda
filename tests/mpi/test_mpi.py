"""MPI works with the env's own mpich, under different UCX transport settings.

Transports compared (the UCX_TLS question):
  shipped: whatever the env's activation sets (e.g. UCX_TLS=tcp)
  tcp:     UCX_TLS=tcp explicitly
  auto:    UCX_TLS unset, so UCX picks the best transport the node offers (InfiniBand on Casper)
Other UCX settings from activation (e.g. UCX_MODULES) are kept in every variant.
Each run: allreduce, 64 MiB ring (bandwidth recorded), parallel netCDF4 + h5py write/read,
and the UCX transports actually used (from UCX_PROTO_INFO). Fails on any `UCX ERROR` line.
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.mpi
for _module in ("mpi4py", "netCDF4", "h5py"):
    pytest.importorskip(_module)

HERE = Path(__file__).resolve().parent
MPIEXEC = Path(sys.prefix) / "bin" / "mpiexec"
TRANSPORTS = ("shipped", "tcp", "auto")
UCX_TRANSPORT_NAMES = re.compile(
    r"\b(tcp|rc_mlx5|rc_verbs|dc_mlx5|ud_mlx5|ud_verbs|sysv|posix|cma|xpmem|knem|self|"
    r"cuda_copy|cuda_ipc|gdr_copy)\b")


def allocation():
    lines = Path(os.environ["PBS_NODEFILE"]).read_text().split()
    nodes = list(dict.fromkeys(lines))
    return nodes, lines.count(nodes[0]), len(lines)


def mpi_env(transport):
    env = dict(os.environ, HDF5_USE_FILE_LOCKING="FALSE", UCX_PROTO_INFO="y")
    if transport == "tcp":
        env["UCX_TLS"] = "tcp"
    elif transport == "auto":
        env.pop("UCX_TLS", None)
    return env


def launch_cmd(mode, tmp_path, hold=0):
    nodes, per_node, total = allocation()
    if mode == "single":
        launch, min_nodes = ["-hosts", nodes[0], "-n", str(per_node)], 1
    else:
        hosts = tmp_path / "hosts.txt"
        hosts.write_text("".join(f"{n}:{per_node}\n" for n in nodes))
        launch, min_nodes = ["-f", str(hosts), "-n", str(total)], 2
    return [str(MPIEXEC), *launch, sys.executable, str(HERE / "mpi_io_test.py"), str(tmp_path),
            "--min-nodes", str(min_nodes), "--hold", str(hold)]


def start(cmd, env, stdout=subprocess.PIPE):
    """mpiexec in its own process group, so a timeout kills proxies and ranks too.
    With the default pipe, read it while waiting (communicate): ranks block once 64 KiB of output is unread."""
    return subprocess.Popen(cmd, env=env, stdout=stdout, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def record_run(record_property, transport, log):
    m = re.search(r"\((\d+) MB/s\)", log)
    record_property("ucx_tls", os.environ.get("UCX_TLS", "<unset>") if transport == "shipped"
                    else ("tcp" if transport == "tcp" else "<unset>"))
    record_property("ring_MBps", m.group(1) if m else "n/a")
    record_property("ucx_transports", ",".join(sorted(set(UCX_TRANSPORT_NAMES.findall(
        "\n".join(l for l in log.splitlines() if "|" in l))))) or "n/a")
    lib = re.search(r"^libs: (.*)$", log, re.M)
    record_property("libraries", lib.group(1) if lib else "n/a")


def check(proc, log):
    assert proc.returncode == 0, log.strip().splitlines()[-1] if log.strip() else proc.returncode
    assert "MPI OK" in log
    errors = [l for l in log.splitlines() if "UCX  ERROR" in l]
    assert not errors, errors[0]


@pytest.fixture
def save_log():
    out = Path(os.environ.get("NPL_TEST_OUT", "."))

    def save(name, text):
        (out / "mpi-logs").mkdir(parents=True, exist_ok=True)
        (out / "mpi-logs" / f"{name}.log").write_text(text)
    return save


@pytest.mark.timeout(420)
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("mode", ["single", "multi"])
def test_mpi_transport(mode, transport, tmp_path, record_property, save_log):
    if not MPIEXEC.exists():
        pytest.skip("env has no mpiexec")
    if mode == "multi" and len(allocation()[0]) < 2:
        pytest.skip("allocation has a single node")
    proc = start(launch_cmd(mode, tmp_path), mpi_env(transport))
    try:
        log = proc.communicate(timeout=360)[0]
    finally:
        kill_group(proc)
    save_log(f"{mode}-{transport}", log)
    record_run(record_property, transport, log)
    check(proc, log)


@pytest.mark.gpu
@pytest.mark.timeout(420)
@pytest.mark.parametrize("transport", ("shipped", "auto"))
def test_mpi_on_gpu_node(transport, tmp_path, record_property, save_log):
    """MPI works on a GPU node. Also records how many ranks the MPI/UCX stack itself made
    open a CUDA context (the test program never touches the GPU, so any context is UCX's)."""
    psutil = pytest.importorskip("psutil")
    if not MPIEXEC.exists():
        pytest.skip("env has no mpiexec")
    # output to a file, not a pipe: this loop doesn't read while it polls, and on Casper UCX_PROTO_INFO prints
    # >64 KiB, which filled the pipe and held every run to the 360 s deadline (2026-11-21)
    log_path = tmp_path / "mpiexec.log"
    with open(log_path, "w") as out:
        proc = start(launch_cmd("single", tmp_path, hold=15), mpi_env(transport), stdout=out)
    most, deadline = 0, time.monotonic() + 360
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            try:
                ours = {c.pid for c in psutil.Process(proc.pid).children(recursive=True)}
            except psutil.NoSuchProcess:
                break
            q = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=30)
            gpu_pids = {int(p) for p in q.stdout.split() if p.strip().isdigit()}
            most = max(most, len(ours & gpu_pids))
            time.sleep(2)
        proc.wait(timeout=max(1, deadline - time.monotonic()))
    finally:
        kill_group(proc)
        log = log_path.read_text()
        save_log(f"gpu-{transport}", log)       # also on a timeout, for diagnosis
    record_run(record_property, transport, log)
    record_property("ranks_with_gpu_context", most)
    check(proc, log)
