# ncar-conda

This repository provides YAML exports of Conda environments deployed on NCAR analysis and HPC clusters including `Casper` and `Derecho`. The environments are also available as kernels in the [NCAR JupyterHub](https://jupyterhub.hpc.ucar.edu/).

## Maintaining NPL environments

There are currently two kinds of NPL releases, the bi-annual production releases (`npl-YYYYa` and `npl-YYYYb`) and a rolling monthly develop env (`npl-dev-YYYY.MM`) that follows conda-forge so problems show up before the next release. To keep the size of the conda environment storage requirements down, we will agree to keep the last three npl-dev releases on rolling availability. Fro example, when `npl-dev-2027.04` is released, then `npl-dev-2027.01` will be deleted.

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

### Setup

1. Clone this repo into your work space and `cd` into it. Run everything below from the repo root.
2. `module load conda`, then check that `conda config --show channels` lists **only** `conda-forge`.
   A `channels:` block in your own `~/.condarc` adds to that list, and the build refuses to run.
3. Install the test harness once per Python version, on a login node: `tests/harness/install.sh <env prefix>`.

Build and test output goes to `/glade/derecho/scratch/$USER/npl-scratch` (set `OUTROOT` to change it).

### Creating new environments

1. **Write the spec.** Copy the newest spec dir of the same kind and edit it:
   ```bash
   mkdir envs/npl/npl-2027a
   cp envs/npl/npl-2026b/{environment.yml,env_vars} envs/npl/npl-2027a/
   # edit name:, python=, and packages in environment.yml; review env_vars
   ```
2. **Build it** in a batch job (the create step took 5–25 min in Nov 2026; the job allows 3 h):
   ```bash
   mkdir -p /glade/derecho/scratch/$USER/npl-scratch/builds
   qsub -A <project> -v SPEC=envs/npl/npl-2027a \
        -o /glade/derecho/scratch/$USER/npl-scratch/builds/ util/build_env.pbs
   ```
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
3. **Read the results** at the end of the job log. They must say MPI builds `ok` and tests `ok`. Look
   through the package changes for major-version jumps.

### Testing

Run the full suite on every node type, against the current release as the control:
```bash
tests/jobs/submit_node_tests.sh -A <project> <control prefix> <candidate prefix>            # prints the qsub commands
tests/jobs/submit_node_tests.sh -A <project> --submit <control prefix> <candidate prefix>   # submits them
```
By default that is six 2-node jobs: Casper CPU, V100, A100, H100 and Derecho CPU, A100 (GPU jobs use
1 GPU per node; expect long queue waits for them). Casper CPU jobs are pinned to genoa nodes and V100 jobs
to cascadelake nodes (`cpu_type`), so results stay comparable from run to run. Options:

| Option | Effect |
|---|---|
| `--scope full` (default) | 2 nodes per node type, the whole suite |
| `--scope single` | 1 node per node type, the whole suite except the multi-node MPI tests (shorter queue waits) |
| `--scope multi` | 2 nodes per node type, only the MPI tests (after an mpich, ucx, mpi4py, hdf5 or netcdf change) |
| `--hw cpu` / `--hw gpu` | only the CPU or only the GPU node types (default: all) |
| `--only casper-cpu,derecho-cpu` | exactly these node types |
| `--candidate-spec <environment.yml>` | needed when the candidate was built from a lock: a lock-built env records every package as requested, and without this the import test tries ~400 C libraries |
| `--control-spec <environment.yml>` | the same for the control |

Results go to the run dir `.../npl-scratch/tests/runs/<date>_<control>-vs-<candidate>/` (the submit
command prints it). Each job updates two reports there when it finishes, each as `.txt` and `.md`:

- **`SUMMARY.txt`**: the check. A verdict (PASS, FAIL, or INCOMPLETE while jobs are pending), then one
  row per test type (import, activate, app serial, app mpi, kernel) and one column per node type, labelled
  with its hardware (e.g. `casper-a100 (milan, a100)`). A cell is FAIL when, compared with the control env,
  a test:
  - fails only in the candidate (**regressed**);
  - passed in the control but is absent from the candidate (**missing**). A package removed from the
    candidate's `environment.yml` is not a FAIL; it is listed under Package differences instead;
  - passed in the control but now skips (**newly skipped**);
  - is new and fails (**new failing**);
  - or got **slower**: MPI bandwidth down 25% or more, or a non-MPI test at least 50% and 2 s slower
    (import/activate tests: 3 s). MPI tests are judged by bandwidth only; their runtime is mostly start-up.
    Tests that submit PBS jobs are not timed at all (their runtime is queue wait).

  Tests that fail in both envs show as `pass (N known)`. A job with no results makes the verdict FAIL.
  Below the table: only the failing tests (with the node types and job IDs where they failed), then **package differences** (requested packages added to or
  removed from the candidate's `environment.yml`, not dependencies).
- **`RESULTS.txt`**: the details, to investigate a FAIL. Overview per node type (counts and total runtime
  per env), every failure group (including fixed and pre-existing), MPI bandwidth for both envs side by
  side, and every runtime change of at least 25% and 0.5 s either way (import and activate: 2 s), and
  the same package differences.

Both name the envs (e.g. `npl-2026b`, `npl-dev-2026.11`) rather than control/candidate. Each test is
described by arch (the CPU, e.g. `genoa`, or GPU, e.g. `a100`, it ran on), comms (`serial`/`mpi`), type
(`import`, `activate`, `app`, `kernel`), test and variant. Each job first runs an untimed import pass for
both envs, because the first read of an env's files on a node is much slower and would otherwise look
like a slowdown.

A job killed at walltime can't update the reports, and two jobs finishing at the same moment can leave
one of them out. In either case, or after changing `report.py`, rebuild them with
`tests/harness/report.py collect <run dir>` on a login node. Raw data and PBS logs are in the same run dir.

Then do the three manual JupyterHub checks in [tests/README.md](tests/README.md). For a quick check on a
login node, run only the smoke tests: `tests/harness/run.sh <prefix> <outdir> -m smoke`.

Any change to mpich, ucx, mpi4py, hdf5 or netcdf needs the multi-node runs, not just the build's
single-node check.

### Commit, install, release

- Commit the spec dir (`environment.yml`, `env_vars`, `versions.yml`). Generated reports stay in scratch.
  Tag each develop month (`git tag -a npl-dev-YYYY.MM`).
- **System install (csgteam)**, recreating exactly what was tested:
  ```bash
  conda env create -p /glade/u/apps/opt/miniforge/envs/npl-2027a -f envs/npl/npl-2027a/versions.yml
  util/install_env_vars /glade/u/apps/opt/miniforge/envs/npl-2027a envs/npl/npl-2027a/env_vars
  ```
  Then create the JupyterHub kernel with `bin/create_conda_kernel`, and move the `npl` symlink when the
  release becomes the default.
- Packages users ask for later go into the released env in place. Add them to its `environment.yml`, and
  regenerate its `versions.yml` with `util/refresh_yaml`.
- Develop envs: keep the 3 newest on disk. Any month can be rebuilt with
  `qsub -A <project> -v SPEC=envs/npl-dev/npl-dev-YYYY.MM,FROM=lock ... util/build_env.pbs`.

## Additional Resources

* [Conda Documentation](https://docs.conda.io/projects/conda)
* [Using Conda at NCAR](https://ncar-hpc-docs.readthedocs.io/en/latest/environment-and-software/user-environment/conda)
* [Setting up ML/AI environments at NCAR](https://github.com/NCAR/aiml_gpu_ncar_envs)
