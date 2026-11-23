#!/bin/bash
#
#   Run the NPL test suite against one conda env, the way users get it:
#   a real `conda activate` (env variables + activate.d scripts), then the env's own python.
#
#   Usage: run.sh ENV_PREFIX OUTDIR [pytest args...]
#     e.g. run.sh /glade/u/apps/opt/miniforge/envs/npl-2026a $OUT/npl-2026a -m smoke
#   Select tests with -m/-k. To run specific files or tests, set NPL_TEST_PATHS (absolute paths or node IDs,
#   space-separated).
#   Set NPL_SPEC=<environment.yml> for envs created from a full lock (see below).
#   Writes: OUTDIR/junit.xml, OUTDIR/fingerprint.json, OUTDIR/environment.yml, OUTDIR/pytest.log
#   Exit:   pytest's exit code (0 all passed, 1 test failures, 2-5 pytest usage/internal errors),
#           or 90-92 for harness problems (not the env's fault).

set -uo pipefail
umask 022

tests=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
prefix=$(readlink -f "${1:?usage: run.sh ENV_PREFIX OUTDIR [pytest args]}")
out=${2:?usage: run.sh ENV_PREFIX OUTDIR [pytest args]}
shift 2
mkdir -p "$out"
out=$(readlink -f "$out")

kit=${NPL_TESTKIT:-/glade/work/$USER/npl-testkit}
pyver=$("$prefix/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])') || exit 90
site=$kit/site-py$pyver
if [[ ! -d $site ]]; then
    "$tests/harness/install.sh" "$prefix" || {
        >&2 echo "Error: no test harness for python $pyver; run tests/harness/install.sh $prefix on a login node"
        exit 90
    }
fi

# Real activation. The conda module does not install the shell hook in non-login shells,
# so source conda.sh from the conda the module points to. (Lmod and conda.sh are not
# nounset-safe, hence set +u.)
set +u
if [[ -z ${CONDA_EXE:-} ]]; then
    module -q load conda 2>/dev/null || { >&2 echo "Error: cannot load the conda module"; exit 91; }
fi
conda_sh=$(dirname "$(dirname "$CONDA_EXE")")/etc/profile.d/conda.sh
source "$conda_sh" || { >&2 echo "Error: cannot source $conda_sh"; exit 91; }
conda activate "$prefix"
set -u
if [[ $(readlink -f "${CONDA_PREFIX:-/nonexistent}") != "$prefix" ]]; then
    >&2 echo "Error: conda activate $prefix did not take effect (CONDA_PREFIX=${CONDA_PREFIX:-unset})"
    exit 92
fi
export NPL_CONDA_SH=$conda_sh    # for tests that must drive conda in a fresh shell

# The env's own package list, exactly as it was requested. An env created from a full lock
# (versions.yml) records every package as requested, so for those pass the month's
# environment.yml as NPL_SPEC instead.
if [[ -n ${NPL_SPEC:-} ]]; then
    cp "$NPL_SPEC" "$out/environment.yml" || { >&2 echo "Error: cannot read NPL_SPEC=$NPL_SPEC"; exit 90; }
    >&2 echo "Package list from NPL_SPEC=$NPL_SPEC (not the env's history)"
else
    conda env export --from-history -p "$prefix" > "$out/environment.yml" \
        || { >&2 echo "Error: could not export the env's package list"; exit 90; }
fi

# test paths or node IDs, split on whitespace only (no globbing: IDs like test_x[a-b] contain brackets)
read -ra paths <<< "${NPL_TEST_PATHS:-$tests}"
unset PYTHONPATH                 # nothing but the env itself on child processes' path
export NPL_TEST_OUT=$out
cd "$out"                        # nothing from the repo lands on sys.path
# pytest is APPENDED to sys.path of the pytest process only, so the env's own packages always
# win and child processes (kernels, MPI ranks, imports) never see the harness.
# (pytest only honors `testpaths` when started from the rootdir, so paths are passed explicitly)
python -c 'import sys; sys.path.append(sys.argv.pop(1)); import pytest; sys.exit(pytest.console_main())' \
    "$site" -c "$tests/pytest.ini" --rootdir "$tests" --basetemp "$out/tmp" \
    --junitxml "$out/junit.xml" -o junit_suite_name="$(basename "$prefix")" \
    --spec "$out/environment.yml" "$@" "${paths[@]}" 2>&1 | tee "$out/pytest.log"
exit "${PIPESTATUS[0]}"
