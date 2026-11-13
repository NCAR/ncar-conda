"""`conda activate` + `conda deactivate` leaves the env's declared variables as they were.

A variable that was unset before activation must be unset again afterwards, not exported as ''
(conda's own `variables:` does the latter, and UCX_TLS='' breaks system openmpi/ucx in that
shell; see envs/npl-dev/env_vars). A preset value must come back unchanged. Checked in
bash and, when installed, tcsh, from a shell without this env active.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

PREFIX = Path(sys.prefix).resolve()
SHELLS = ("bash", "tcsh")


def fresh_env(names):
    """os.environ without this env's activation and without any of `names`."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CONDA_", "_CE_", "NPL_SAVED_", "__CONDA_")) and k not in names}
    env["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                  if p and not os.path.realpath(p).startswith(str(PREFIX))
                                  and "condabin" not in p)
    return env


def script(shell, conda_sh, names):
    if shell == "bash":
        show = "; ".join(f'if [ -n "${{{k}+x}}" ]; then echo "$1 {k}=SET:${{{k}}}"; '
                         f'else echo "$1 {k}=UNSET"; fi' for k in names)
        return (f'show() {{ {show}; }}\nsource "{conda_sh}"\nshow before\n'
                f'conda activate "{PREFIX}"\nshow active\nconda deactivate\nshow after\n'
                'echo "after LEFTOVER=$(env | grep -c ^NPL_SAVED_)"\n')
    conda_csh = str(Path(conda_sh).with_name("conda.csh"))
    lines = [f'source "{conda_csh}"']
    for phase in ("before", "active", "after"):
        if phase == "active":
            lines.append(f'conda activate "{PREFIX}"')
        if phase == "after":
            lines.append("conda deactivate")
        for k in names:
            lines += [f"printenv {k} >& /dev/null",
                      f'if ( $status == 0 ) then\n echo "{phase} {k}=SET:`printenv {k}`"\n'
                      f'else\n echo "{phase} {k}=UNSET"\nendif']
    lines.append('echo "after LEFTOVER=`env | grep -c ^NPL_SAVED_`"')
    return "\n".join(lines) + "\n"


def states(output):
    seen = {}
    for line in output.splitlines():
        phase, _, rest = line.partition(" ")
        if phase in ("before", "active", "after") and "=" in rest:
            k, _, v = rest.partition("=")
            seen[(phase, k)] = v
    return seen


@pytest.mark.parametrize("preset", (None, "value", ""), ids=("unset-before", "preset-before", "empty-before"))
@pytest.mark.parametrize("shell", SHELLS)
def test_deactivate_restores_variables(shell, preset, declared_env_vars, tmp_path):
    names = sorted(declared_env_vars)
    if not names:
        pytest.skip("env declares no variables")
    conda_sh = os.environ.get("NPL_CONDA_SH")
    if not conda_sh:
        pytest.skip("NPL_CONDA_SH not set (run through harness/run.sh)")
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")
    env = fresh_env(names)
    if preset is not None:
        env.update({k: preset and f"npl-preset-{k}" for k in names})
    path = tmp_path / f"cycle.{shell}"
    path.write_text(script(shell, conda_sh, names))
    cmd = ["bash", "--noprofile", "--norc", str(path)] if shell == "bash" else ["tcsh", "-f", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    seen = states(proc.stdout)
    leftover = seen.pop(("after", "LEFTOVER"), None)
    assert len(seen) == 3 * len(names), f"{shell} run incomplete:\n{proc.stdout}\n{proc.stderr[-2000:]}"

    problems = []
    for k in names:
        before = seen[("before", k)]
        if seen[("active", k)] != f"SET:{declared_env_vars[k]}":
            problems.append(f"{k} while active: {seen[('active', k)]} (expected SET:{declared_env_vars[k]})")
        if seen[("after", k)] != before:
            problems.append(f"{k} after deactivate: {seen[('after', k)]} (was {before})")
    if leftover != "0":
        problems.append(f"{leftover} NPL_SAVED_* variables left after deactivate")
    assert not problems, "; ".join(problems)
