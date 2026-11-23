# ncar-conda

This repository provides YAML exports of Conda environments deployed on NCAR analysis and HPC clusters including `Casper` and `Derecho`. The environments are also available as kernels in the [NCAR OnDemand's Jupyter](https://ondemand.hpc.ucar.edu/).

## Maintaining NPL environments

There are two kinds of NPL releases that CSG maintains: the bi-annual production releases (`npl-YYYYa` and `npl-YYYYb`) and a rolling monthly develop env (`npl-dev-YYYY.MM`) that follows conda-forge so problems show up before the next release. To keep the size of the conda environment storage requirements down, we will agree to keep the last three npl-dev releases on rolling availability. For example, when `npl-dev-2027.04` is released, then `npl-dev-2027.01` will be deleted.

### Layout

| Path | Contents |
|---|---|
| `envs/npl/npl-YYYY[ab]/` | a release's spec dir |
| `envs/npl-dev/npl-dev-YYYY.MM/` | a develop month's spec dir |
| `<spec dir>/environment.yml` | requested packages (**edit this** to change the env) |
| `<spec dir>/env_vars` | environment variables the env sets on activation (`KEY=VALUE`, edited by hand) |
| `<spec dir>/versions.yml` | the lock: every package as `name=version=build` (generated, never hand-edited) |
| `util/build_env.pbs` | batch job: create, install env vars, export lock, check MPI builds, quick tests, report |
| `util/install_env_vars` | installs `env_vars` and its activate/deactivate scripts into an env |
| `util/diff_versions.py` | markdown diff of two `versions.yml` files |
| `tests/` | the test suite (see [tests/README.md](tests/README.md)) |

## Setup

1. Clone this repo into your work space and `cd` into it. Run everything below from the repo root.
2. `module load conda`, then check that `conda config --show channels` lists **only** `conda-forge`.
   A `channels:` block in your own `~/.condarc` adds to that list, and the build refuses to run.
3. Install the test harness once per Python version, on a login node: `tests/harness/install.sh <env prefix>`.

Build and test output goes to `/glade/derecho/scratch/$USER/npl-scratch` by default (set `OUTROOT` to change it).

## Creating new environments

### 1. **Write the spec** 
Copy the newest spec dir of the same kind and edit it:
   ```bash
   mkdir envs/npl/npl-2027a
   cp envs/npl/npl-2026b/{environment.yml,env_vars} envs/npl/npl-2027a/
   # edit name:, python=, and packages in environment.yml; review env_vars
   ```
### 2. **Build it**
   ```bash
   mkdir -p /glade/derecho/scratch/$USER/npl-scratch/builds
   qsub -A <project> -v SPEC=envs/npl/npl-2027a \
        -o /glade/derecho/scratch/$USER/npl-scratch/builds/ util/build_env.pbs
   ```
   <details>
   <summary>Expand for details</summary>

   It creates `/glade/work/$USER/conda-envs/npl-2027a` and writes `versions.yml` into the spec dir (only if
   the spec dir has none yet; otherwise the new lock stays in `.../npl-scratch/builds/npl-2027a/`).
   The job header lists every option. Useful ones:
   - `FROM=lock`: by default the build solves `environment.yml`, so it gets the newest conda-forge
     packages that fit. `FROM=lock` skips the solve and installs exactly the packages and builds listed in
     a lock file (`versions.yml`), so the result is identical to the env that lock came from. Use it to
     rebuild an old env, or to stage a release as a copy of a tested develop month:
     `-v SPEC=envs/npl/npl-2027a,FROM=lock,LOCK=envs/npl-dev/npl-dev-2026.12/versions.yml`.
   - `PREFIX=<path>`: build somewhere else (the job stops if the prefix already exists).
   - `REPORT=1`: also save the results as `.../npl-scratch/builds/npl-2027a/REPORT.md`.

   </details>

### 3. **Read the results** 
They must say MPI builds `ok` and tests `ok`. Look through the package changes for major-version jumps.

## Testing

Run the full suite on every node type, against the current release as the control:
```bash
# prints the plan: node types, hardware, test types
tests/jobs/submit_node_tests.sh -A <project> <control prefix> <candidate prefix>

# submits the jobs (add --show-qsub to also print the qsub commands)
tests/jobs/submit_node_tests.sh -A <project> --submit <control prefix> <candidate prefix>   
```

  `<control prefix>` = the current or older environment used as baseline. Ex. `/glade/u/apps/opt/conda/envs/npl-2025b`

  `<candidate prefix>` = the new test environment. Ex `/glade/work/$USER/conda-envs/npl-dev-2027.11`

By default that is six 2-node jobs: Casper CPU, V100, A100, H100 and Derecho CPU, A100 (GPU jobs use
1 GPU per node). Casper CPU jobs are pinned to genoa nodes and V100 jobs
to cascadelake nodes (`cpu_type`), so results stay comparable from run to run. 

Which test types each mode runs:

| Mode | Nodes per job | Walltime | import | activate | app serial | app mpi, 1 node | app mpi, 2 nodes | kernel |
|---|---|---|---|---|---|---|---|---|
| `--scope full` (default) | 2 | 0:45 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `--scope single` | 1 | 0:45 | ✓ | ✓ | ✓ | ✓ | | ✓ |
| `--scope multi` | 2 | 0:30 | | | | ✓ | ✓ | |

Additional options:
 
| Option | Tests Run |
|---|---|
| `--hw cpu` / `--hw gpu` | only the CPU or only the GPU node types (default: all) |
| `--only casper-cpu,derecho-cpu` | exactly these node types |
| `--candidate-spec <environment.yml>` | needed when the candidate was built from a lock: a lock-built env records every package as requested, and without this the import test tries ~400 C libraries |
| `--control-spec <environment.yml>` | the same for the control |

On GPU node types, app mpi also runs MPI on one GPU node (shipped and unset `UCX_TLS`) and counts the
ranks that open a CUDA context. The test that submits its own PBS job (a one-worker dask `PBSCluster`)
runs only on the CPU node types in full and single scope; elsewhere it skips.

### Test types

These are the rows of `SUMMARY.txt`. Test files are in [tests/](tests/README.md).

| Type | Files | Checks |
|---|---|---|
| import | `smoke/test_imports.py` | each requested package imports (or its command runs) |
| activate | `smoke/test_activation.py` | `conda activate` sets the env's variables; `conda deactivate` restores the previous values (bash, tcsh) |
| app serial | `functional/` | small workloads with known answers: file I/O (netCDF, HDF5, zarr, GRIB, HDF4), dask, GDAL/geopandas, xESMF, uxarray, plotting, numba/scipy/scikit-learn |
| app mpi | `mpi/` | the env's mpich: allreduce, ring bandwidth, parallel netCDF4/h5py I/O, with each `UCX_TLS` setting, on 1 and 2 nodes |
| kernel | `jupyterstack/` | a kernel from `bin/create_conda_kernel` starts and activates the env; matplotlib, widgets, dask, lab extensions, server proxy |

Results go to the run dir `.../npl-scratch/tests/runs/<date>_<control>-vs-<candidate>/` (the submit
command prints it). Each job updates two reports there when it finishes, each as `.txt` and `.md`:

- **`SUMMARY.[txt|md]`**: Basic output to quickly determine if a new environment is performing similarly or better than
  the previous. It's strict and shows a fail if even a single test fails. Use judgement and the RESULTS output to determine
  if a true fail. Because the way packages resolve and set dependencies, new versions of modules can sometimes introduce small
  performance differences.

  Each test is compared between the two envs and reports a result from the table based on the test:

  | Control env | Candidate env | Reported as | Cell |
  |---|---|---|---|
  | pass or skip | fail | **regressed** | FAIL |
  | pass | absent | **missing** | FAIL |
  | any | import test absent or skipped, its package removed from the candidate's `environment.yml` | listed under Package differences (other tests of that package still count) | pass |
  | pass | skip | **newly skipped** | FAIL |
  | absent (new test) | fail | **new failing** | FAIL |
  | pass | pass, but slower (next table) | **slower** | FAIL |
  | fail | fail | pre-existing (fail in both) | `pass (N known)` |
  | fail | pass or absent | fixed (in RESULTS.txt only) | pass |

  | Test type | Metric | Slower = FAIL when |
  |---|---|---|
  | MPI | ring bandwidth (runtime is mostly start-up; no bandwidth figure = not judged) | bandwidth down 25% or more |
  | import, activate | runtime | at least 50% **and** 3 s slower |
  | other non-MPI | runtime | at least 50% **and** 2 s slower |
  | submits a PBS job | not timed (runtime is queue wait) | never |

  A job with no results makes the verdict FAIL.

  Below the pass/fail table: only the failing tests (with the node types and job IDs where they failed), then **package differences** (requested packages added to or
  removed from the candidate's `environment.yml`, not dependencies).
- **`RESULTS.[txt|md]`**: the details, to investigate a FAIL. Overview per node type (counts and total runtime
  per env), every failure group (including fixed and pre-existing), MPI bandwidth for both envs side by
  side, and every runtime change of at least 25% and 0.5 s either way (import and activate: 2 s), and
  the same package differences.

  Both name the envs (e.g. `npl-2026b`, `npl-dev-2026.11`) rather than control/candidate. Each test is
  described by these columns:

  | Column | Meaning | Examples |
  |---|---|---|
  | arch | hardware it ran on, maps to PBS select options for either `cpu_type` or `gpu_type` | `genoa`, `milan`, `a100` |
  | comms | `mpi` for tests marked `mpi`, else `serial` | `serial`, `mpi` |
  | type | test type, from the test file (see Test types above) | `import`, `activate`, `app`, `kernel` |
  | test | test function name without `test_` | `package usable`, `mpi transport` |
  | variant | the test's parameter, if any (MPI ones spelled out) | `xarray`, `bash-unset-before`, `2 nodes, UCX tcp` |

Before release, you may want to use the create_conda_kernel script to generate a kernel in the test environment and do the Jupyter kernel checks in [tests/README.md](tests/README.md).

More test information is in [tests/README.md](tests/README.md): every test, how timing noise is kept
out of the reports, rebuilding the reports after a failed job, login-node checks, and when the
multi-node MPI runs are required.

## Commit, install, release

### 1. Commit the spec changes 
  Typically the files changed will be: (`environment.yml`, `env_vars`, `versions.yml`). Generated reports stay in scratch.
  Optionally tag a release, ex: (`git tag -a npl-dev-YYYY.MM`).

### 2. System install (csgteam)
  Recreating exactly what was tested:
  ```bash
  conda env create -p /glade/u/apps/opt/miniforge/envs/npl-2027a -f envs/npl/npl-2027a/versions.yml
  util/install_env_vars /glade/u/apps/opt/miniforge/envs/npl-2027a envs/npl/npl-2027a/env_vars
  ```
  Then create the JupyterHub kernel with `bin/create_conda_kernel`, and move the `npl` symlink when the
  release becomes the default.

### 3. Adding new packages to existing NPL (csgteam)
Packages users ask for later are installed into the released env in place, then its YAMLs are re-exported:
```bash
conda install -p /glade/u/apps/opt/miniforge/envs/npl-2026a --override-channels -c conda-forge <package>
util/refresh_yaml     # interactive; on main, in sync with origin; no channels: in ~/.condarc
```
`refresh_yaml` checks each deployed env against its YAMLs and, when you confirm, rewrites both
`environment.yml` and `versions.yml` from the installed env (found by name under `/glade/u/apps/opt/miniforge/envs`;
no need to activate it). It doesn't commit: review `git diff`, then commit both. It has no `-h`; any arguments are ignored.

## Environment variables (`env_vars`)

Each spec dir has an `env_vars` file (`KEY=VALUE`, one per line) with the variables the env sets on
`conda activate`. `util/install_env_vars` (run by the build) copies it into `<prefix>/etc/npl/env_vars` and
installs the activate/deactivate scripts from `util/npl-env-vars/` for bash/zsh and tcsh.

| Variable | Value | Why |
|---|---|---|
| `UCX_TLS` | `tcp` | NPL's long-standing MPI transport setting |
| `UCX_MODULES` | `^*gda*` | ucx 1.22's GPUDirect Async plugin prints `UCX ERROR cuDeviceGetCount` on every rank of a node without a GPU |
| `HYDRA_LAUNCHER`, `HYDRA_LAUNCHER_EXEC` | `rsh`, `npl-pbs-rsh` | mpiexec starts ranks on other nodes through PBS instead of ssh, which is refused between Derecho GPU nodes; outside a PBS job it falls back to ssh |

**Why not conda's `variables:` block?** On `conda deactivate`, conda leaves a variable that was unset before
activation exported as an empty string. `UCX_TLS=''` then breaks any UCX-based MPI started later from that
shell, including system openmpi on Casper ("UCX ERROR The TLs list is empty"). The `env_vars` scripts save the
previous state and restore it exactly: unset stays unset, and a preset value (even an empty one) comes back.
`tests/smoke/test_activation.py` checks this.

- **Users:** to use other settings, change them after `conda activate` (e.g. `unset UCX_TLS` lets UCX use
  InfiniBand between Casper nodes). `conda deactivate` still restores the pre-activation values.
- **Maintainers:** to change an installed env, edit the spec's `env_vars` and rerun
  `util/install_env_vars <prefix> <env_vars file>`. Never set the same keys in conda's `variables:` too; the
  installer refuses. If an env already has one there, delete it from `"env_vars"` in `<prefix>/conda-meta/state`:
  `conda env config vars unset` only marks it `***unset***`, and deactivate still exports `''`.


## Additional Resources

* [Conda Documentation](https://docs.conda.io/projects/conda)
* [Using Conda at NCAR](https://ncar-hpc-docs.readthedocs.io/en/latest/environment-and-software/user-environment/conda)
* [Setting up ML/AI environments at NCAR](https://github.com/NCAR/aiml_gpu_ncar_envs)
