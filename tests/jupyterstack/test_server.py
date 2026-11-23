"""JupyterLab extensions are installed, and jupyter-server-proxy routes to a local service
(the mechanism the dask dashboard uses on JupyterHub)."""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.jupyter

# lab extensions NPL users rely on: name as shown by `jupyter labextension list`
EXTENSIONS = {
    "dask-labextension": "dask-labextension",
    "ipywidgets": "@jupyter-widgets/jupyterlab-manager",
    "ipympl": "jupyter-matplotlib",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, token=None, timeout=5):
    req = urllib.request.Request(url, headers={"Authorization": f"token {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")


@pytest.mark.timeout(120)
@pytest.mark.parametrize("package", sorted(EXTENSIONS))
def test_lab_extension_enabled(package):
    pytest.importorskip(package.replace("-", "_"))
    jupyter = shutil.which("jupyter") or pytest.fail("jupyter command not on PATH")
    proc = subprocess.run([jupyter, "labextension", "list"], capture_output=True, text=True, timeout=90)
    listing = proc.stdout + proc.stderr
    lines = [l for l in listing.splitlines() if EXTENSIONS[package] in l]
    assert lines, f"{EXTENSIONS[package]} not listed:\n{listing[-1500:]}"
    assert any("enabled" in l for l in lines), f"{EXTENSIONS[package]} not enabled: {lines}"


@pytest.mark.timeout(180)
def test_server_proxy_routes_to_local_service(tmp_path):
    pytest.importorskip("jupyter_server_proxy")
    marker = "npl-proxy-check"
    (tmp_path / "index.html").write_text(marker)
    backend_port, server_port, token = free_port(), free_port(), "npltest"

    log = open(tmp_path / "jupyter-server.log", "w")
    backend = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(backend_port), "--bind", "127.0.0.1",
         "--directory", str(tmp_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = subprocess.Popen(
        [shutil.which("jupyter") or "jupyter", "server", "--no-browser",
         "--ServerApp.ip=127.0.0.1", f"--ServerApp.port={server_port}",
         # ServerApp.token works on jupyter_server 1.x and (deprecated alias) 2.x
         f"--ServerApp.token={token}", f"--ServerApp.root_dir={tmp_path}"],
        stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "JUPYTER_RUNTIME_DIR": str(tmp_path / "runtime")})
    base = f"http://127.0.0.1:{server_port}"
    try:
        deadline = time.time() + 90
        while True:
            try:
                if get(f"{base}/api/status", token)[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            if server.poll() is not None or time.time() > deadline:
                log.flush()
                pytest.fail("jupyter server did not start:\n"
                            + (tmp_path / "jupyter-server.log").read_text()[-2000:], pytrace=False)
            time.sleep(1)
        status, body = get(f"{base}/proxy/{backend_port}/index.html", token, timeout=15)
        assert status == 200 and marker in body
    finally:
        for p in (server, backend):
            p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        log.close()
