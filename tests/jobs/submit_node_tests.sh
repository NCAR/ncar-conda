#!/bin/bash
#
#   Submit tests/jobs/node_test.pbs (control vs candidate) on NPL node types:
#   Casper CPU, V100, A100, H100 and Derecho CPU, A100. All jobs of one submission share a run dir,
#   and each job rebuilds SUMMARY.txt (pass/fail per node type and test type) and RESULTS.txt (details)
#   there when it finishes, so they always cover every finished node type. Prints the qsub commands; submits only with --submit.
#
#   Usage: submit_node_tests.sh -A PROJECT [options] CONTROL_PREFIX CANDIDATE_PREFIX
#     --scope full|single|multi  full (default): 2 nodes per node type, whole suite
#                                single: 1 node per node type, whole suite (multi-node MPI tests skip)
#                                multi: 2 nodes per node type, only the mpi tests (30 min walltime)
#     --hw all|cpu|gpu           node types to test (default all)
#     --only L1,L2               only these node types (casper-cpu casper-v100 casper-a100 casper-h100
#                                derecho-cpu derecho-a100)
#     --candidate-spec FILE      environment.yml to test against when CANDIDATE was built from a lock
#     --control-spec FILE        same for CONTROL
#     --submit                   actually run the qsub commands (default: print them only)
#     OUTROOT=<dir> in the environment changes the output root (default /glade/derecho/scratch/$USER/npl-scratch)
#   Run from a Casper login node. Derecho jobs go to develop@desched1, which accepts jobs from Casper.
#   Results: $OUTROOT/tests/runs/<date>_<control>-vs-<candidate>/SUMMARY.txt and RESULTS.txt (and .md);
#   rebuild them by hand with tests/harness/report.py collect <run dir>

set -euo pipefail
tests=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
account="" submit=0 only="" cand_spec="" ctrl_spec="" scope=full hw=all pos=()
while [[ $# -gt 0 ]]; do
    case $1 in
        -A) account=$2; shift 2 ;;
        --submit) submit=1; shift ;;
        --scope) scope=$2; shift 2 ;;
        --hw) hw=$2; shift 2 ;;
        --only) only=$2; shift 2 ;;
        --candidate-spec) cand_spec=$(readlink -f "$2"); shift 2 ;;
        --control-spec) ctrl_spec=$(readlink -f "$2"); shift 2 ;;
        -h|--help) awk 'NR > 2 && /^#/ {print; next} NR > 2 {exit}' "$0"; exit 0 ;;
        -*) >&2 echo "Error: unknown option $1"; exit 2 ;;
        *) pos+=("$1"); shift ;;
    esac
done
[[ -n $account && ${#pos[@]} -eq 2 ]] || { >&2 echo "usage: $0 -A PROJECT [--scope full|single|multi] [--hw all|cpu|gpu] [--submit] CONTROL CANDIDATE"; exit 2; }
[[ $scope =~ ^(full|single|multi)$ ]] || { >&2 echo "Error: --scope must be full, single or multi"; exit 2; }
[[ $hw =~ ^(all|cpu|gpu)$ ]] || { >&2 echo "Error: --hw must be all, cpu or gpu"; exit 2; }
control=$(readlink -f "${pos[0]}"); candidate=$(readlink -f "${pos[1]}")
for p in "$control" "$candidate"; do
    [[ -x $p/bin/python ]] || { >&2 echo "Error: $p is not an env"; exit 2; }
done
# results are stored per env name
[[ $(basename "$control") != $(basename "$candidate") ]] || { >&2 echo "Error: control and candidate have the same name"; exit 2; }
outroot=${OUTROOT:-/glade/derecho/scratch/$USER/npl-scratch}
rundir=$outroot/tests/runs/$(date +%Y-%m-%d_%H%M%S)_$(basename "$control")-vs-$(basename "$candidate")

nodes=2; walltime=01:30:00; scope_args=""
[[ $scope == single ]] && nodes=1
[[ $scope == multi ]] && { walltime=00:30:00; scope_args="-m mpi"; }
cpu="select=$nodes:ncpus=4:mpiprocs=4"
gpu="select=$nodes:ncpus=4:mpiprocs=4:ngpus=1:mem=32GB"
# cpu_type pins the Casper types that span several CPUs (Nov 2026: htc genoa/cascadelake/skylake,
# v100 cascadelake/skylake), so runs are comparable over time. a100 (milan), h100 (sapphirerapids) and
# Derecho (milan) have one CPU each.
#        label         queue             resources                                    kind
table=(  "casper-cpu   casper            $cpu:mem=32GB:cpu_type=genoa                 cpu"
         "casper-v100  casper            $gpu:gpu_type=v100:cpu_type=cascadelake      gpu"
         "casper-a100  casper            $gpu:gpu_type=a100     gpu"
         "casper-h100  casper            $gpu:gpu_type=h100     gpu"
         "derecho-cpu  develop@desched1  $cpu:mem=16GB          cpu"
         "derecho-a100 develop@desched1  $gpu                   gpu" )

vars="TESTS=$tests,CONTROL=$control,CANDIDATE=$candidate,RUNDIR=$rundir"
[[ -n $cand_spec ]] && vars+=",CANDIDATE_SPEC=$cand_spec"
[[ -n $ctrl_spec ]] && vars+=",CONTROL_SPEC=$ctrl_spec"

selected=()
for row in "${table[@]}"; do
    read -r label _ _ kind <<< "$row"
    [[ $hw == all || $hw == "$kind" ]] || continue
    [[ -z $only || ,$only, == *",$label,"* ]] && selected+=("$label")
done
[[ ${#selected[@]} -gt 0 ]] || { >&2 echo "Error: no node type matches --hw $hw${only:+ --only $only}"; exit 2; }
for l in ${only//,/ }; do
    [[ " ${selected[*]} " == *" $l "* ]] || { >&2 echo "Error: --only $l is not a node type, or not in --hw $hw"; exit 2; }
done

if (( submit )); then
    [[ ! -e $rundir ]] || { >&2 echo "Error: $rundir already exists"; exit 2; }
    mkdir -p "$rundir/logs"
    { echo "control=$control"; echo "candidate=$candidate"; echo "scope=$scope"; echo "hw=$hw"
      echo "submitted=$(date '+%F %H:%M')"; } > "$rundir/run.txt"
fi
failed=0
for row in "${table[@]}"; do
    read -r label queue res kind <<< "$row"
    [[ " ${selected[*]} " == *" $label "* ]] || continue
    args=$scope_args
    # CPU jobs of the full/single suite also test submitting PBS jobs
    [[ $kind == cpu && $scope != multi ]] && args+=" --run-pbs --account=$account"
    v="$vars,LABEL=$label"
    [[ -n ${args# } ]] && v+=",PYTEST_ARGS=${args# }"
    cmd=(qsub -A "$account" -q "$queue" -l "$res" -l place=scatter -l walltime=$walltime
         -v "$v" -o "$rundir/logs/" "$tests/jobs/node_test.pbs")
    if (( submit )); then
        jobid=$("${cmd[@]}") || { jobid=SUBMIT-FAILED; failed=1; }   # keep going with the other node types
        echo "job $label $jobid" >> "$rundir/run.txt"
        echo "$label  $jobid"
    else
        # same command, -v quoted (it may contain a space) so it can be copy-pasted
        echo "qsub -A $account -q $queue -l $res -l place=scatter -l walltime=$walltime -v \"$v\" -o $rundir/logs/ $tests/jobs/node_test.pbs"
    fi
done
if (( submit )); then
    echo "Pass/fail (updated as each job finishes): $rundir/SUMMARY.txt; details: RESULTS.txt (both also .md)"
    exit $failed
else
    echo "# Printed only. Rerun with --submit (that also creates the run dir and its run.txt)."
fi
