#!/usr/bin/env python3
"""MPI communication + parallel netCDF4/h5py I/O check.

Run with: mpiexec -n N python mpi_io_test.py OUTDIR [--min-nodes K]
Fails (nonzero exit) if any check fails or ranks span fewer than K distinct hosts.
"""
from mpi4py import MPI  # initialize MPI before the MPI-linked I/O libraries

import argparse
import os
import socket
import time

import h5py
import netCDF4
import numpy as np

def loaded_libs():
    """Paths of the MPI/UCX/HDF5/netCDF shared libraries actually mapped into this process."""
    keys = ("libmpi.so", "libucp.so", "libhdf5.so", "libnetcdf.so")
    with open("/proc/self/maps") as f:
        return sorted({l.split()[-1] for l in f if any(k in l for k in keys)})


comm = MPI.COMM_WORLD
rank, size = comm.rank, comm.size
p = argparse.ArgumentParser()
p.add_argument("outdir")
p.add_argument("--min-nodes", type=int, default=1)
p.add_argument("--hold", type=float, default=0, help="seconds to stay alive at the end (for GPU snapshots)")
args = p.parse_args()

# Placement
hosts = comm.allgather(socket.gethostname())
nnodes = len(set(hosts))

# Communication: collective + large point-to-point ring (64 MiB, rendezvous protocol)
total = comm.allreduce(rank, op=MPI.SUM)
assert total == size * (size - 1) // 2, "allreduce mismatch"
n = 8 * 1024 * 1024
send = np.full(n, rank, dtype="f8")
recv = np.empty(n, dtype="f8")
comm.Sendrecv(send, dest=(rank + 1) % size, recvbuf=recv, source=(rank - 1) % size)
assert (recv == (rank - 1) % size).all(), "ring sendrecv mismatch"

# Rough ring bandwidth: 5 timed 64 MiB exchanges, slowest rank counts
comm.Barrier()
t0 = time.perf_counter()
for _ in range(5):
    comm.Sendrecv(send, dest=(rank + 1) % size, recvbuf=recv, source=(rank - 1) % size)
dt = comm.allreduce((time.perf_counter() - t0) / 5, op=MPI.MAX)
ring_mbs = send.nbytes / dt / 1e6

# Parallel I/O, file names unique per job
tag = os.environ.get("PBS_JOBID", "nojob").split(".")[0]
nc_path = os.path.join(args.outdir, f"mpi_test_{tag}_{size}.nc")
h5_path = os.path.join(args.outdir, f"mpi_test_{tag}_{size}.h5")

with netCDF4.Dataset(nc_path, "w", parallel=True, comm=comm, info=MPI.Info()) as ds:
    ds.createDimension("x", size)
    v = ds.createVariable("v", "i4", ("x",))
    v[rank] = rank

with h5py.File(h5_path, "w", driver="mpio", comm=comm) as f:
    d = f.create_dataset("v", (size,), dtype="i4")
    d[rank] = rank

comm.Barrier()
if args.hold:
    time.sleep(args.hold)
    comm.Barrier()
if rank == 0:
    expect = np.arange(size)
    with netCDF4.Dataset(nc_path) as ds:
        assert (ds["v"][:] == expect).all(), "netCDF4 parallel write mismatch"
    with h5py.File(h5_path) as f:
        assert (f["v"][:] == expect).all(), "h5py mpio write mismatch"
    os.remove(nc_path)
    os.remove(h5_path)
    assert nnodes >= args.min_nodes, f"ranks on {nnodes} host(s), need >= {args.min_nodes}: {hosts}"
    print(f"MPI OK on {size} ranks / {nnodes} node(s) ({','.join(sorted(set(hosts)))}): "
          f"allreduce, 64MiB ring ({ring_mbs:.0f} MB/s), netCDF4 {netCDF4.__version__}, h5py {h5py.__version__}; "
          f"{MPI.Get_library_version().splitlines()[0].strip()}")
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(os.sys.executable)))
    libs = loaded_libs()
    foreign = [l for l in libs if not l.startswith(prefix)]
    print(f"libs: {'all from env' if not foreign else 'FOREIGN: ' + ' '.join(foreign)} ({len(libs)} checked)")
