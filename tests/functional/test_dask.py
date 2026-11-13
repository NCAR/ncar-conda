"""Dask clusters start and compute: a local cluster everywhere, a PBS cluster on request."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.functional
distributed = pytest.importorskip("distributed")

# Where a one-worker PBSCluster job should go on each system
PBS_QUEUE = {"casper": "casper", "derecho": "develop"}


@pytest.mark.timeout(240)
def test_local_cluster_computes(tmp_path):
    da = pytest.importorskip("dask.array")
    from distributed import Client, LocalCluster

    with LocalCluster(n_workers=2, threads_per_worker=1, processes=True,
                      dashboard_address=":0", local_directory=str(tmp_path)) as cluster, \
            Client(cluster) as client:
        assert client.submit(sum, range(1000)).result(timeout=60) == 499500
        x = da.ones((2000, 2000), chunks=500)
        assert float(x.sum().compute()) == 4_000_000
        assert len(client.scheduler_info()["workers"]) == 2
        assert client.dashboard_link


@pytest.mark.pbs
@pytest.mark.timeout(900)
def test_pbs_cluster_one_worker(request, tmp_path):
    """The replacement path for ncar-jobqueue: dask-jobqueue straight on PBS."""
    jobqueue = pytest.importorskip("dask_jobqueue")
    from distributed import Client

    account = request.config.getoption("--account")
    if not account:
        pytest.skip("no --account given")
    queue = PBS_QUEUE.get(os.environ.get("NCAR_HOST", ""))
    if not queue:
        pytest.skip(f"no PBS queue known for NCAR_HOST={os.environ.get('NCAR_HOST')!r}")
    logs = Path(os.environ.get("NPL_TEST_OUT", tmp_path)) / "dask-worker-logs"

    cluster = jobqueue.PBSCluster(
        account=account, queue=queue, cores=1, processes=1, memory="4GB",
        resource_spec="select=1:ncpus=1:mem=4GB", walltime="00:10:00",
        local_directory=str(tmp_path), log_directory=str(logs),
        scheduler_options={"dashboard_address": ":0"},
    )
    try:
        cluster.scale(jobs=1)
        with Client(cluster) as client:
            client.wait_for_workers(1, timeout=600)
            assert client.submit(pow, 6, 2).result(timeout=120) == 36
    finally:
        cluster.close()
