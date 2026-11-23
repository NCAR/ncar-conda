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
| `harness/report.py` | `slower` lists tests to rerun; `summary` of one run; `compare` control vs candidate; `collect` all node types of one submission into `SUMMARY.txt` (pass/fail) and `RESULTS.txt` (details), each also `.md` |
| `data/packages.yml` | conda name → import name / command checks |
| `smoke/` | every top-level package imports (`smoke`) |
| `functional/` | I/O round-trips, dask, regridding, plotting, GDAL/geopandas, GRIB/HDF4, unstructured grids (uxarray), numerics (`functional`) |
| `jupyterstack/` | kernel made with `create_conda_kernel`, widgets, ipympl, dask in kernel, lab extensions, server-proxy (`jupyter`) |
| `mpi/` | MPI under UCX transports shipped / tcp / auto, GPU contexts (`mpi`, `gpu`) |
| `jobs/node_test.pbs` | PBS runner on one node type: untimed import warm-up, control + candidate, reruns of tests flagged slower, then compare |
| `jobs/submit_node_tests.sh` | prints the plan; `--submit` runs `node_test.pbs` per node type, `--show-qsub` prints the qsub commands (`--scope full/single/multi`, `--hw all/cpu/gpu`); one run dir with `SUMMARY.txt` and `RESULTS.txt` |

## Markers

`smoke`, `functional`, `jupyter`, `mpi` (needs PBS allocation), `gpu` (needs NVIDIA GPU),
`network` (needs internet), `pbs` (submits jobs; `--run-pbs --account=X`), `timeout(s)`.

Each result also gets report labels (`conftest.py`, stored as junit properties): `arch` (`gpu` if marked `gpu`,
else `cpu`), `comms` (`mpi` if marked `mpi`, else `serial`) and `type` from the file: `import`
(`smoke/test_imports`), `activate` (`smoke/test_activation`), `kernel` (`jupyterstack/`), `app` (everything else).
`harness/report.py` uses them for the arch/comms/type/test/variant columns, and shows arch as the hardware:
the CPU microarchitecture (from the model name in `fingerprint.json`, e.g. `genoa`) or the GPU model
(`gpus` in `fingerprint.json`, e.g. `a100`; for older results, from the node-type label).

<details>
<summary><b>Every test, by report type</b></summary>

Tests skip when their package is absent (or the node lacks a GPU, internet or a PBS allocation).

**import** (`smoke/test_imports.py`)
- `test_package_usable[<package>]`: one test per requested package (98 for npl-2026b); imports it, or runs its command
  (`data/packages.yml`), in a fresh interpreter

**activate** (`smoke/test_activation.py`)
- `test_deactivate_restores_variables[<shell>-<preset>]`: 6 variants, bash and tcsh × variable unset /
  preset / preset empty before activation; values are right while active and restored after deactivate

**app serial** (`functional/`)
- File I/O (`test_io.py`, `test_formats.py`)
  - `test_netcdf_roundtrip[netcdf4]`, `[h5netcdf]`: xarray dataset with a noleap calendar
  - `test_zarr_roundtrip`, `test_blosc_compression_roundtrip`, `test_h5py_compressed_roundtrip`
  - `test_cfgrib_reads_grib`, `test_pygrib_reads_grib`: GRIB2 written from an eccodes sample
  - `test_hdf4_roundtrip`
- Dask (`test_dask.py`, `test_io.py`)
  - `test_local_cluster_computes`: 2-worker `LocalCluster`, dashboard link
  - `test_dask_chunked_reduction_matches_numpy`: lazy chunked netCDF mean
  - `test_pbs_cluster_one_worker` (`pbs`): one-worker dask-jobqueue `PBSCluster`
- Geospatial and regridding (`test_geo.py`, `test_regrid.py`, `test_unstructured.py`)
  - `test_gdal_geotiff_write_and_reproject`, `test_shapely_geometry`,
    `test_geopandas_reproject_and_file_roundtrip`
  - `test_bilinear_matches_analytic_field`, `test_conservative_preserves_global_integral`: xESMF
  - `test_uxarray_grid_geometry`, `test_uxarray_remap_integrate_zonal_mean`,
    `test_uxarray_ugrid_file_roundtrip`: octahedron grid
- Plotting (`test_plotting.py`)
  - `test_matplotlib_renders_png`, `test_cartopy_projected_plot_without_downloads`
  - `test_cartopy_coastlines_download` (`network`): Natural Earth download
- Numerics (`test_compute.py`)
  - `test_numba_parallel_jit`, `test_scipy_quadrature`, `test_sklearn_linear_regression`

**app mpi** (`mpi/test_mpi.py`; allreduce, 64 MiB ring bandwidth, parallel netCDF4/h5py I/O)
- `test_mpi_transport[<mode>-<transport>]`: 6 variants, `single` (1 node) and `multi` (2 nodes) ×
  `UCX_TLS` shipped / `tcp` / `auto` (unset)
- `test_mpi_on_gpu_node[shipped]`, `[auto]` (`gpu`): 1 node; counts ranks that open a CUDA context

**kernel** (`jupyterstack/`)
- Kernel from `bin/create_conda_kernel` (`test_kernel.py`)
  - `test_kernelspec_uses_launcher`, `test_kernel_runs_env_python`, `test_kernel_has_activated_environment`
  - `test_inline_matplotlib_figure`, `test_ipywidgets_open_a_comm`, `test_ipympl_widget_backend`,
    `test_dask_cluster_inside_kernel`
- JupyterLab server (`test_server.py`)
  - `test_lab_extension_enabled[dask-labextension]`, `[ipympl]`, `[ipywidgets]`
  - `test_server_proxy_routes_to_local_service`

</details>

## Running

```bash
<repo>/tests/harness/install.sh <env prefix>                               # once per python version
<repo>/tests/harness/run.sh <env prefix> <outdir> -m smoke                 # quick, any node
<repo>/tests/jobs/submit_node_tests.sh -A <project> <control> <candidate>  # full run, all node types (prints the plan; add --submit)
python3 <repo>/tests/harness/report.py compare <control outdir> <candidate outdir>
```

Do not run `functional`, `jupyter` or `mpi` on login nodes; use `jobs/node_test.pbs`. For a quick
check on a login node, run only the smoke tests: `<repo>/tests/harness/run.sh <prefix> <outdir> -m smoke`.

Any change to mpich, ucx, mpi4py, hdf5 or netcdf needs the multi-node runs (`--scope full` or
`--scope multi`), not just the build's single-node check.

### Timing noise

To keep one-off noise out of the timings, each job first runs an untimed pass of the import tests for
both envs (the first read of an env's files on a node is much slower), and after the timed runs it reruns
any test flagged slower twice more per env. The reports use the mean of those reruns for that test, so a
slowdown counts only if it holds up.

### Rebuilding the reports

Each job updates `SUMMARY` and `RESULTS` in the run dir when it finishes. A job killed at walltime can't
update them, and two jobs finishing at the same moment can leave one of them out. In either case, or
after changing `report.py`, rebuild them with `<repo>/tests/harness/report.py collect <run dir>` on a login node.
Raw data and PBS logs are in the same run dir.

## Manual checks

On https://ondemand.hpc.ucar.edu, with the candidate kernel in a Jupyter instance:
1. The kernel appears in the launcher with the NPL logo and starts.
2. A dask `LocalCluster` dashboard opens in the dask-labextension side panel.
3. An `ipywidgets` slider and a `%matplotlib widget` figure are interactive.
