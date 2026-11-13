# NPL test suite

Tests that a conda env (any NPL version) works the way users get it on Casper and Derecho.
The suite lives outside the envs it tests and does not depend on the rest of this repo,
except `bin/create_conda_kernel` for the kernel tests (override with `NPL_KERNEL_CREATOR`).

## Principles

- **Env-agnostic.** Package lists come from the env itself (`conda env export --from-history`). For an env created from a full lock
  (`versions.yml`), every package counts as requested, so set `NPL_SPEC` to that month's `environment.yml`.
  Tests skip when their package is absent and assert behavior, never versions.
- **Harness outside the env.** pytest is installed once per Python version into
  `/glade/work/$USER/npl-testkit/site-pyX.Y` and put on `PYTHONPATH`; the env is never modified.
- **As users get it.** `harness/run.sh` does a real `conda activate` (env variables and
  `activate.d` scripts) and runs the env's own python. Default login modules stay loaded.
- **Release vs candidate.** Runs always test a control (current release) and a candidate.
  `report.py compare` sorts failures into regressions, pre-existing and fixed.
- **Self-contained.** Synthetic data only, per-run scratch dirs, no shared datasets.
  Tests that need internet, GPUs, a PBS allocation, or that submit jobs are marked and
  skip (with a reason) where they can't run.

## Layout

| Path | Contents |
|---|---|
| `harness/install.sh` | install pytest outside the env (login node; needs internet) |
| `harness/run.sh` | activate env, run pytest, write `junit.xml` + `fingerprint.json` |
| `harness/report.py` | `summary` of one run; `compare` control vs candidate |
| `data/packages.yml` | conda name → import name / command checks |
| `smoke/` | every top-level package imports (`smoke`) |
| `functional/` | I/O round-trips, dask, regridding, plotting, GDAL/geopandas, GRIB/HDF4, numerics (`functional`) |
| `jupyterstack/` | kernel made with `create_conda_kernel`, widgets, ipympl, dask in kernel, lab extensions, server-proxy (`jupyter`) |
| `mpi/` | MPI under UCX transports shipped / tcp / auto, GPU contexts (`mpi`, `gpu`) |
| `jobs/node_test.pbs` | PBS runner: control + candidate on one node type, then compare |

## Markers

`smoke`, `functional`, `jupyter`, `mpi` (needs PBS allocation), `gpu` (needs NVIDIA GPU),
`network` (needs internet), `pbs` (submits jobs; `--run-pbs --account=X`), `timeout(s)`.

## Running

```bash
T=/glade/work/$USER/nplplan/ncar-conda/tests
$T/harness/install.sh <env prefix>                               # once per python version
$T/harness/run.sh <env prefix> <outdir> -m smoke                 # quick, any node
qsub ... $T/jobs/node_test.pbs                                   # full run; see header
python3 $T/harness/report.py compare <control outdir> <candidate outdir>
```

Do not run `functional`, `jupyter` or `mpi` on login nodes; use `jobs/node_test.pbs`.

## Manual checks (cannot be automated)

On https://jupyterhub.hpc.ucar.edu, with the candidate kernel:
1. The kernel appears in the launcher with the NPL logo and starts.
2. A dask `LocalCluster` dashboard opens in the dask-labextension side panel.
3. An `ipywidgets` slider and a `%matplotlib widget` figure are interactive.
