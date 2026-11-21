#!/usr/bin/env python3
"""Markdown reports from the suite's JUnit XML (standard library only).

  report.py summary RUN_DIR                    one env: counts, failures, MPI table
  report.py compare CONTROL_DIR CANDIDATE_DIR  release vs candidate: regressions first
  report.py collect SUBMISSION_DIR [--ended JOBID]   all node types of one submit_node_tests.sh run ->
                                               SUBMISSION_DIR/SUMMARY.{txt,md} (pass/fail) and
                                               RESULTS.{txt,md} (details)

RUN_DIR is an OUTDIR written by harness/run.sh (junit.xml + fingerprint.json).
"""
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ORDER = ("failed", "error", "skipped", "passed")


class RunMissing(Exception):
    pass


def load(run_dir):
    run_dir = Path(run_dir)
    if not (run_dir / "junit.xml").exists():
        raise RunMissing(f"no junit.xml in {run_dir} (harness error, or killed at walltime); "
                         f"see {run_dir}/pytest.log")
    root = ET.parse(run_dir / "junit.xml").getroot()
    cases = {}
    for tc in root.iter("testcase"):
        test_id = f"{tc.get('classname', '')}::{tc.get('name')}"
        status, detail = "passed", ""
        for tag, name in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                status, detail = name, (el.get("message") or el.text or "").strip()
                break
        props = {p.get("name"): p.get("value") for p in tc.iter("property")}
        cases[test_id] = {"status": status, "detail": detail.splitlines()[0][:200] if detail else "",
                          "props": props, "time": float(tc.get("time") or 0)}
    fp_path = run_dir / "fingerprint.json"
    fp = json.loads(fp_path.read_text()) if fp_path.exists() else {}
    return cases, fp


def short(test_id):
    """'functional.test_io::test_netcdf_roundtrip[h5netcdf]' -> 'test_io::test_netcdf_roundtrip[h5netcdf]'"""
    classname, _, name = test_id.partition("::")
    return f"{classname.rsplit('.', 1)[-1]}::{name}"


def env_name(fp):
    return Path(fp.get("prefix", "?")).name


def counts(cases):
    c = {s: 0 for s in ORDER}
    for v in cases.values():
        c[v["status"]] += 1
    return c


def mpi_table(cases):
    rows = [(k, v) for k, v in sorted(cases.items()) if "ring_MBps" in v["props"]]
    if not rows:
        return []
    out = ["| test | result | UCX_TLS | UCX transports used | ring MB/s | GPU contexts | libraries |",
           "|---|---|---|---|---|---|---|"]
    for k, v in rows:
        p = v["props"]
        out.append(f"| {short(k)} | {v['status']} | {p.get('ucx_tls')} | {p.get('ucx_transports')} | "
                   f"{p.get('ring_MBps')} | {p.get('ranks_with_gpu_context', '')} | {p.get('libraries')} |")
    return out


def summary(run_dir):
    cases, fp = load(run_dir)
    c = counts(cases)
    lines = [f"### {env_name(fp)} on {fp.get('host', '?')}", "",
             f"{len(cases)} tests: {c['passed']} passed, {c['failed']} failed, "
             f"{c['error']} errors, {c['skipped']} skipped", ""]
    bad = [(k, v) for k, v in sorted(cases.items()) if v["status"] in ("failed", "error")]
    if bad:
        lines += ["| failing test | detail |", "|---|---|"]
        lines += [f"| {short(k)} | {v['detail'].replace('|', '/')} |" for k, v in bad]
        lines.append("")
    lines += mpi_table(cases)
    return "\n".join(lines)


GROUPS = ("regression", "missing", "newly skipped", "pre-existing", "fixed", "new failing", "removed")
WORSE = ("regression", "missing", "newly skipped", "new failing")     # these make a summary cell FAIL


def group_titles(old, new):
    return {"regression": f"Regressions (fail only in {new})",
            "missing": f"Missing in {new} (passed in {old}, absent now: package dropped?)",
            "newly skipped": f"Newly skipped (passed in {old}, skipped in {new})",
            "pre-existing": "Pre-existing (fail in both)",
            "fixed": f"Fixed (fail only in {old})",
            "new failing": f"New tests failing (only in {new})",
            "removed": f"Removed from {new}'s spec (see Package differences)"}


def classify(ctl, cand, removed=()):
    """{group: [(test_id, detail)]} for the old env's (ctl) vs the new env's (cand) cases. removed: packages
    taken out of the new env's spec; import tests of those that are absent or skipped now go to 'removed'
    (shown under Package differences, not as a failure)."""
    bad = ("failed", "error")
    groups = {g: [] for g in GROUPS}
    for k in sorted(set(ctl) | set(cand)):
        a, b = ctl.get(k), cand.get(k)
        lab = labels(k, (b or a)["props"])
        if lab[2] == "import" and lab[4].lower() in removed and (b is None or b["status"] == "skipped"):
            groups["removed"].append((k, "package removed from the spec"))
        elif a is None:
            if b["status"] in bad:
                groups["new failing"].append((k, b["detail"]))
        elif b is None:
            if a["status"] == "passed":
                groups["missing"].append((k, "passed before"))
            elif a["status"] in bad:
                groups["fixed"].append((k, "removed; failed before"))
        elif b["status"] in bad and a["status"] not in bad:
            groups["regression"].append((k, b["detail"]))
        elif b["status"] in bad and a["status"] in bad:
            groups["pre-existing"].append((k, b["detail"]))
        elif a["status"] in bad and b["status"] == "passed":
            groups["fixed"].append((k, a["detail"]))
        elif a["status"] == "passed" and b["status"] == "skipped":
            groups["newly skipped"].append((k, b["detail"]))
    return groups


def compare(control_dir, candidate_dir):
    ctl, ctl_fp = load(control_dir)
    cand, cand_fp = load(candidate_dir)
    try:
        added, removed = package_diff(control_dir, candidate_dir)
        pkg_line = f"added {', '.join(sorted(added)) or 'none'}; removed {', '.join(sorted(removed)) or 'none'}"
    except OSError:
        added, removed, pkg_line = set(), set(), "unknown (no saved environment.yml)"
    groups = classify(ctl, cand, removed)
    old, new = env_name(ctl_fp), env_name(cand_fp)
    lines = [f"## {new} vs {old} on {cand_fp.get('host', '?')}", ""]
    lines.append(f"**Package differences:** {pkg_line}")
    lines.append("")
    for g, items in groups.items():
        if g == "removed":
            continue
        lines.append(f"**{group_titles(old, new)[g]}: {len(items)}**")
        lines += [f"- `{short(k)}`: {d.replace('|', '/')}" for k, d in items]
        lines.append("")
    for name, cases in ((env_name(ctl_fp), ctl), (env_name(cand_fp), cand)):
        table = mpi_table(cases)
        if table:
            lines += [f"MPI runs, {name}:", ""] + table + [""]
    return "\n".join(lines)


# ---------------------------------------------------------------- collect (one submission)
# A document is a list of blocks, rendered as markdown and as plain text:
#   ("h", level, text)  ("p", text)  ("list", [items])  ("table", [headers], [[cells]])
#   ("kv", [[label, value, ...]])  aligned columns without a header

def read_manifest(sub_dir):
    """run.txt from submit_node_tests.sh: key=value lines, plus 'job LABEL JOBID' lines."""
    meta, jobs = {}, []
    for line in (Path(sub_dir) / "run.txt").read_text().splitlines():
        if line.startswith("job "):
            _, label, jobid = line.split(None, 2)
            jobs.append((label, jobid))
        elif "=" in line:
            k, _, v = line.partition("=")
            meta[k] = v
    return meta, jobs


def job_result(sub_dir, label, jobid, ctl_name, cand_name, ended=()):
    """(state, data): ('done', (ctl, cand, hardware)), ('no result', reason), ('submit failed', '') or ('pending', '').
    ended: job IDs known to have finished (the job running collect; its own log is not copied yet)."""
    if jobid == "SUBMIT-FAILED":
        return "submit failed", "qsub failed; see the submit command's output"
    try:
        ctl, _ = load(Path(sub_dir) / label / ctl_name)
        cand, fp = load(Path(sub_dir) / label / cand_name)
        return "done", (ctl, cand, hardware(fp, label))
    except (ET.ParseError, ValueError) as e:      # cut off mid-write, e.g. killed at walltime
        return "no result", f"unreadable result file ({e}); see logs/{jobid}.OU"
    except RunMissing:
        # the PBS log exists from job start, so ask PBS whether the job is still queued or running
        state = "F" if jobid in ended else pbs_state(jobid)
        if state == "F":
            return "no result", f"job ended without results; see logs/{jobid}.OU"
        if state is None:
            return "unknown", "qstat failed; rerun collect later"
        return "pending", {"Q": "queued", "R": "running"}.get(state, f"PBS state {state}")


def pbs_state(jobid):
    """PBS job_state letter; 'F' also when PBS no longer knows the job; None if qstat itself failed."""
    import subprocess
    try:
        p = subprocess.run(["qstat", "-x", "-f", jobid], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in p.stdout.splitlines():
        if line.strip().startswith("job_state ="):
            return line.split("=", 1)[1].strip()
    if "Unknown Job Id" in p.stdout + p.stderr:
        return "F"           # out of PBS history: long finished
    return None


def fmt_counts(cases):
    c = counts(cases)
    out = f"{c['passed']} pass, {c['failed'] + c['error']} fail"
    return out + (f", {c['skipped']} skip" if c["skipped"] else "")


# ---- readable labels and units
LABEL_COLS = ["arch", "comms", "type", "test", "variant"]
MPI_VARIANT = {"single": "1 node", "multi": "2 nodes", "auto": "UCX auto", "shipped": "UCX shipped",
               "tcp": "UCX tcp"}


def labels(test_id, props):
    """[arch, comms, type, test, variant]. arch/comms/type are recorded by conftest.py; results from before
    that are labelled from the file name the same way."""
    classname, _, name = test_id.partition("::")
    func, _, param = name.partition("[")
    param = param.rstrip("]")
    pkg = classname.split(".")[0]
    kind = {"test_imports": "import", "test_activation": "activate"}.get(classname.rsplit(".", 1)[-1],
                                                                       "kernel" if pkg == "jupyterstack" else "app")
    comms = props.get("comms", "mpi" if pkg == "mpi" else "serial")
    arch = props.get("arch", "gpu" if "gpu" in func else "cpu")
    if comms == "mpi":
        param = ", ".join(MPI_VARIANT.get(t, t) for t in param.split("-")) if param else ""
    return [arch, comms, props.get("type", kind), func.removeprefix("test_").replace("_", " "), param]


def fmt_time(sec):
    if round(sec, 2) < 10:
        return f"{sec:.2f} s"
    sec = round(sec)
    return f"{sec} s" if sec < 60 else f"{sec // 60} min {sec % 60:02d} s"


def fmt_bw(mbps):
    try:
        return f"{float(mbps) / 1000:.1f} GB/s"
    except (TypeError, ValueError):
        return "n/a"


def fmt_change(old, new):
    try:
        old, new = float(old), float(new)
    except (TypeError, ValueError):
        return ""
    if not (old > 0 and math.isfinite(old) and math.isfinite(new)):
        return ""
    return f"{round((new - old) / old * 100):+d}%"


def runtime_changed(kind, old, new):  # what RESULTS lists; FAIL uses slower_fail
    """25% either way and at least 0.5 s (2 s for import/activate tests, which vary more)."""
    if old <= 0 or abs(new - old) / old < 0.25:
        return False
    return abs(new - old) >= (2 if kind in ("import", "activate") else 0.5)


def span(values, fmt):
    lo, hi = min(values), max(values)
    return fmt(lo) if fmt(lo) == fmt(hi) else f"{fmt(lo)} to {fmt(hi)}"


# ---- hardware names
def cpu_arch(model):
    """Microarchitecture from a /proc/cpuinfo model name; the model name itself if unknown."""
    m = re.search(r"EPYC (\d)[\dA-Z]{2}(\d)", model)
    if m:
        name = {("7", "1"): "naples", ("7", "2"): "rome", ("7", "3"): "milan", ("9", "4"): "genoa",
                ("9", "5"): "turin"}.get(m.groups())
        if name:
            return name
    m = re.search(r"(?:Platinum|Gold|Silver|Bronze) \d(\d)\d\d", model)
    if m:
        name = {"1": "skylake", "2": "cascade_lake", "3": "ice_lake", "4": "sapphire_rapids",
                "5": "emerald_rapids"}.get(m.group(1))
        if name:
            return name
    return model or "cpu"


def gpu_arch(names, label):
    """'NVIDIA A100-SXM4-40GB' -> 'a100'. Results from before GPU names were recorded: from the node-type label."""
    for n in names:
        m = re.search(r"\b([A-Z]\d{2,3}S?)\b", n)
        if m:
            return m.group(1).lower()
    if names:
        return names[0]
    m = re.search(r"-([a-z]\d{2,3}s?)$", label)
    return m.group(1) if m else "gpu"


def hardware(fp, label):
    """{'cpu': codename, 'gpu': model}: the arch shown for CPU tests and for GPU tests on this node type."""
    return {"cpu": cpu_arch(fp.get("cpu", "")), "gpu": gpu_arch(fp.get("gpus", []), label)}


def hw_text(hw):
    """'genoa' on a CPU node, 'milan, a100' on a GPU node (gpu_arch gives plain 'gpu' when there is none)."""
    return hw["cpu"] if hw["gpu"] == "gpu" else f"{hw['cpu']}, {hw['gpu']}"


def arch_names(lab, hits, hw):
    """Arch column of a row merged across node types: the hardware the test used on each of them."""
    return ", ".join(dict.fromkeys(hw[n][lab[0]] for n in hits))


# ---- pass/fail: what makes a summary cell FAIL besides the WORSE groups
BW_DROP_FAIL = 0.25          # MPI ring bandwidth 25% or more lower
RT_SLOWER_FAIL = 0.50        # runtime 50% or more higher, and at least 2 s (import/activate: 3 s)


def bw_fail(old, new):
    try:
        old, new = float(old), float(new)
    except (TypeError, ValueError):
        return False
    return old > 0 and math.isfinite(new) and (old - new) / old >= BW_DROP_FAIL


def slower_fail(kind, comms, old, new):
    """MPI tests are judged by bandwidth: their runtime is mostly mpiexec start-up, which varies."""
    if comms == "mpi" or old <= 0:
        return False
    return (new - old) / old >= RT_SLOWER_FAIL and new - old >= (3 if kind in ("import", "activate") else 2)


def queue_timed(test_id, props):
    """Tests that submit PBS jobs: their runtime is mostly queue wait, so it is never judged or listed.
    conftest records pbs=1; older results are recognised by the test name."""
    return props.get("pbs") == "1" or "pbs" in test_id.partition("::")[2].partition("[")[0]


SUMMARY_ROWS = ("import", "activate", "app serial", "app mpi", "kernel")
REASON = {"regression": "regressed", "missing": "missing", "newly skipped": "newly skipped",
          "new failing": "new failing", "slower": "slower"}


def summary_row(lab):
    return f"app {lab[1]}" if lab[2] == "app" else lab[2]


def problems(ctl, cand, removed=()):
    """(failing items [(test_id, labels, reason, detail, size)], pre-existing [(test_id, labels)], groups);
    size ranks 'slower' items (fraction slower) so merged rows can show the worst node type."""
    groups = classify(ctl, cand, removed)
    lab = lambda k: labels(k, (cand.get(k) or ctl.get(k))["props"])
    out = [(k, lab(k), g, d, 0) for g in WORSE for k, d in groups[g]]
    for k in sorted(set(ctl) & set(cand)):
        a, b = ctl[k], cand[k]
        if not a["status"] == "passed" == b["status"]:
            continue
        ra, rb = a["props"].get("ring_MBps"), b["props"].get("ring_MBps")
        if bw_fail(ra, rb):
            out.append((k, lab(k), "slower", f"bandwidth {fmt_bw(ra)} -> {fmt_bw(rb)} ({fmt_change(ra, rb)})",
                        1 - float(rb) / float(ra)))
        elif not queue_timed(k, b["props"]) and slower_fail(lab(k)[2], lab(k)[1], a["time"], b["time"]):
            out.append((k, lab(k), "slower", f"runtime {fmt_time(a['time'])} -> {fmt_time(b['time'])} "
                                              f"({fmt_change(a['time'], b['time'])})", b["time"] / a["time"] - 1))
    return out, [(k, lab(k)) for k, _ in groups["pre-existing"]], groups


def summary_cell(row, probs, known, ctl, cand):
    n = {}
    for _, lab, reason, _, _ in probs:
        if summary_row(lab) == row:
            n[reason] = n.get(reason, 0) + 1
    if n:
        return "FAIL: " + ", ".join(f"{c} {REASON[r]}" for r, c in n.items())
    statuses = [v["status"] for cases in (ctl, cand) for k, v in cases.items()
                if summary_row(labels(k, v["props"])) == row]
    if not statuses:
        return "-"
    if all(s == "skipped" for s in statuses):
        return "skip"
    k = sum(1 for _, lab in known if summary_row(lab) == row)
    return "pass" + (f" ({k} known)" if k else "")


def gather(sub_dir, ended=()):
    meta, jobs = read_manifest(sub_dir)
    old, new = Path(meta["control"]).name, Path(meta["candidate"]).name
    results = {label: (jobid, *job_result(sub_dir, label, jobid, old, new, ended)) for label, jobid in jobs}
    pending = [l for l, r in results.items() if r[1] in ("pending", "unknown")]
    head = [["New:", new, meta["candidate"]], ["Old:", old, meta["control"]],
            ["Scope:", meta.get("scope", "?")], ["Hardware:", meta.get("hw", "?")],
            ["Submitted:", meta.get("submitted", "?")]]
    finished = (f"{len(results) - len(pending)} of {len(results)}"
                + (f" (pending: {', '.join(pending)})" if pending else ""))
    pkgs = None
    for label in results:
        try:
            pkgs = package_diff(Path(sub_dir) / label / old, Path(sub_dir) / label / new)
            break
        except OSError:
            continue
    return meta, old, new, results, head, finished, pkgs


def spec_packages(run_dir):
    """Top-level (requested) package names from the environment.yml that run.sh saved with the results."""
    names, inside, indent = set(), False, None
    for line in (Path(run_dir) / "environment.yml").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("dependencies:"):
            inside = True
        elif inside and not line[0].isspace() and not line.startswith("-"):
            break                                  # next top-level key
        elif inside:
            # list items at the first item's indent: '- name', '- name=1.2', '- h5py[build=*mpich*]',
            # '- "name<2"', '- conda-forge::name'. Deeper items (under '- pip:') are not conda packages.
            m = re.match(r"^( *)- +[\"']?(?:[\w.-]+::)?([A-Za-z0-9_][\w.-]*) *(?:[=<>!~\[ \"']|$)", line)
            if m and (indent is None or len(m.group(1)) == indent):
                indent = len(m.group(1))
                names.add(m.group(2).lower())
            elif indent is None and re.match(r"^ *- ", line):
                indent = len(line) - len(line.lstrip())  # e.g. '- pip:' first
    return names


def package_diff(old_dir, new_dir):
    """(added, removed) requested packages; OSError when a run has no saved environment.yml."""
    a, b = spec_packages(old_dir), spec_packages(new_dir)
    return b - a, a - b


def with_jobs(nodes, results):
    """'casper-cpu (6781893.casper-pbs), derecho-cpu (8267664.desched1)': where to look (logs/<job>.OU)."""
    return ", ".join(f"{n} ({results[n][0]})" for n in nodes)


def package_block(pkgs, new, gone=None):
    """gone: {test_id: node types} of import tests that went with a removed package (details report only)."""
    if pkgs is None:
        return [("p", "Package differences: unknown (no saved environment.yml)")]
    added, removed = pkgs
    rows = [[f"Added to {new}:", ", ".join(sorted(added)) or "none"],
            [f"Removed from {new}:", ", ".join(sorted(removed)) or "none"]]
    if gone is not None:
        rows.append(["Their import tests:", f"{len(gone)} not run in {new} (not counted as missing)"])
    return [("h", 2, "Package differences (requested packages, not dependencies)"), ("kv", rows)]


def fail_rules(old, new):
    return (f"A summary cell is FAIL when, against {old}, a test fails only in {new} (regressed), passed in {old} "
            f"but is absent from {new} (missing), passed in {old} but skips in {new} (newly skipped), is new and "
            f"fails (new failing), or got substantially slower: MPI bandwidth down {BW_DROP_FAIL:.0%} or more, or "
            f"a non-MPI test {RT_SLOWER_FAIL:.0%} and 2 s (import/activate 3 s) or more slower (tests that submit "
            f"PBS jobs are not timed: queue wait). A test gone because its package was removed from {new}'s spec "
            f"has its import test listed under Package differences, not as missing (other tests of it that now skip "
            f"still count as newly skipped). 'pass (N known)': "
            f"N tests fail in both envs. skip: could not run there (no GPU, no package). -: not in this run's "
            f"scope. A job without results makes the verdict FAIL.")


def summary_doc(sub_dir, ended=()):
    meta, old, new, results, head, _, pkgs = gather(sub_dir, ended)
    removed = pkgs[1] if pkgs else set()
    cols, rows, failing, failed_jobs = [], {r: [] for r in SUMMARY_ROWS}, {}, []
    hw = {}
    for label, (jobid, state, data) in results.items():
        if state != "done":
            cols.append(label)
            for r in SUMMARY_ROWS:
                rows[r].append(state)
            if state in ("no result", "submit failed"):
                failed_jobs.append(f"{label}: {state} ({data})")
            continue
        ctl, cand, hw[label] = data
        cols.append(f"{label} ({hw_text(hw[label])})")
        probs, known, _ = problems(ctl, cand, removed)
        for r in SUMMARY_ROWS:
            rows[r].append(summary_cell(r, probs, known, ctl, cand))
        for k, lab, reason, detail, size in probs:
            entry = failing.setdefault((k, reason), [lab, [], detail, size, label])
            entry[1].append(label)
            if size > entry[3]:
                entry[2:] = [detail, size, label]
    pending = any(r[1] in ("pending", "unknown") for r in results.values())
    if failing or failed_jobs:
        verdict = f"FAIL ({len(failing)} failing tests" + (f", {len(failed_jobs)} jobs without results" if failed_jobs else "") + ")"
    elif pending:
        verdict = "INCOMPLETE (no failures so far; jobs pending)"
    else:
        verdict = "PASS"
    table_rows = [[r] + rows[r] for r in SUMMARY_ROWS if any(c != "-" for c in rows[r])]
    doc = [("h", 1, f"NPL node tests: {new} vs {old}"), ("kv", head),
           ("h", 2, f"Verdict: {verdict}"),
           ("table", ["test type"] + cols, table_rows)]
    if failed_jobs:
        doc += [("h", 2, "Jobs without results"), ("list", failed_jobs)]
    if failing:
        doc += [("h", 2, f"Failing tests: {len(failing)}"),
                ("table", LABEL_COLS + ["node types (job)", "why", "detail"],
                 [[arch_names(lab, nodes, hw)] + lab[1:] + [with_jobs(nodes, results), REASON[reason],
                   f"worst {worst}: {d}" if reason == "slower" and len(nodes) > 1 else d]
                  for (k, reason), (lab, nodes, d, _, worst) in sorted(failing.items(), key=lambda kv: kv[1][0])])]
    doc += package_block(pkgs, new)       # after the failing tests: those matter more
    doc.append(("p", f"Details: {sub_dir}/RESULTS.txt"))
    return doc


def collect_doc(sub_dir, ended=()):
    meta, old, new, results, head, finished, pkgs = gather(sub_dir, ended)
    removed = pkgs[1] if pkgs else set()
    titles = group_titles(old, new)
    doc = [("h", 1, f"NPL node tests, details: {new} vs {old}"), ("kv", head + [["Jobs finished:", finished]]),
           ("p", fail_rules(old, new))]
    done = {label: data for label, (jobid, state, data) in results.items() if state == "done"}
    hw = {label: data[2] for label, data in done.items()}

    # overview, and failures merged across node types
    rows, merged = [], {g: {} for g in GROUPS}
    for label, (jobid, state, data) in results.items():
        if state != "done":
            rows.append([label, "", jobid, state] + [""] * 7 + [data])
            continue
        ctl, cand, _ = data
        groups = classify(ctl, cand, removed)
        rows.append([label, hw_text(hw[label]), jobid, "done",
                     fmt_counts(ctl), fmt_counts(cand),
                     fmt_time(sum(v["time"] for v in ctl.values())),
                     fmt_time(sum(v["time"] for v in cand.values())),
                     str(len(groups["regression"])), str(len(groups["pre-existing"])), str(len(groups["fixed"])), ""])
        for g, items in groups.items():
            for k, detail in items:
                props = (cand.get(k) or ctl.get(k))["props"]
                merged[g].setdefault(k, (labels(k, props), [], detail))[1].append(label)
    doc += [("h", 2, "Overview"),
            ("table", ["node type", "hardware", "job", "state", old, new, f"{old} time", f"{new} time",
                       "regressions", "pre-existing", "fixed", "note"], rows)]
    for g in GROUPS:
        items = merged[g]
        if g == "removed" or (not items and g != "regression"):
            continue
        doc.append(("h", 2, f"{titles[g]}: {len(items)}"))
        if items:
            doc.append(("table", LABEL_COLS + ["node types (job)", "detail"],
                        [[arch_names(lab, nodes, hw)] + lab[1:] + [with_jobs(nodes, results), d] for k, (lab, nodes, d) in
                         sorted(items.items(), key=lambda kv: kv[1][0])]))
        else:
            doc.append(("p", "None."))

    doc += package_block(pkgs, new, merged["removed"])       # after the failure groups

    # MPI bandwidth, both envs side by side
    mpi_rows = []
    for label, (ctl, cand, _) in done.items():
        for k in sorted(set(ctl) | set(cand), key=lambda k: labels(k, (cand.get(k) or ctl.get(k))["props"])):
            a, b = ctl.get(k, {"props": {}}), cand.get(k, {"props": {}})
            if "ring_MBps" not in a["props"] and "ring_MBps" not in b["props"]:
                continue
            ta, tb = a["props"].get("ucx_transports", ""), b["props"].get("ucx_transports", "")
            lab = labels(k, b["props"] or a["props"])
            mpi_rows.append([label, hw[label][lab[0]], lab[3], lab[4], ta if ta == tb else f"{ta} -> {tb}",
                             fmt_bw(a["props"].get("ring_MBps")), fmt_bw(b["props"].get("ring_MBps")),
                             fmt_change(a["props"].get("ring_MBps"), b["props"].get("ring_MBps")),
                             b["props"].get("ranks_with_gpu_context", "")])
    if mpi_rows:
        doc += [("h", 2, "MPI bandwidth (64 MiB ring, slowest rank)"),
                ("table", ["node type", "arch", "test", "variant", f"UCX transports ({old} -> {new})",
                           old, new, "change", "GPU contexts"], mpi_rows)]

    # runtime changes of tests that passed in both, merged across node types: a test is listed if it changed
    # on any node type; the ranges cover every node type where it passed in both envs
    runs = {}
    for label, (ctl, cand, _) in done.items():
        for k in set(ctl) & set(cand):
            a, b = ctl[k], cand[k]
            if a["status"] == "passed" == b["status"] and a["time"] > 0 and not queue_timed(k, b["props"]):
                runs.setdefault(k, (labels(k, b["props"]), []))[1].append((label, a["time"], b["time"]))
    rt_rows = []
    for k, (lab, hits) in runs.items():
        changed = [h[0] for h in hits if runtime_changed(lab[2], h[1], h[2])]
        if not changed:
            continue
        ratios = [b / a for _, a, b in hits]
        rt_rows.append((max(abs(x - 1) for x in ratios), [arch_names(lab, changed, hw)] + lab[1:] + [
            f"{', '.join(changed)} ({len(changed)} of {len(hits)})", span([h[1] for h in hits], fmt_time),
            span([h[2] for h in hits], fmt_time), span([(x - 1) * 100 for x in ratios], lambda v: f"{v:+.0f}%")]))
    doc.append(("h", 2, f"Runtime changes (25% and 0.5 s or more; import/activate 2 s): {len(rt_rows)} tests"))
    if rt_rows:
        doc.append(("p", "Ranges cover every node type where the test passed in both envs."))
        doc.append(("table", LABEL_COLS + ["changed on", old, new, "change"],
                    [r for _, r in sorted(rt_rows, key=lambda x: -x[0])]))
    else:
        doc.append(("p", "None."))
    doc.append(("p", f"Raw data per node type: {sub_dir}/<node type>/ (junit.xml, pytest.log); "
                     f"PBS logs: {sub_dir}/logs/"))
    return doc


def render_md(doc):
    out = []
    for b in doc:
        if b[0] == "h":
            out += ["#" * b[1] + " " + b[2], ""]
        elif b[0] == "p":
            out += [b[1], ""]
        elif b[0] == "list":
            out += [f"- {i}" for i in b[1]] + [""]
        elif b[0] == "kv":
            out += [f"- **{r[0]}** {r[1]}" + (f" (`{r[2]}`)" if len(r) > 2 else "") for r in b[1]] + [""]
        elif b[0] == "table":
            cell = lambda x: str(x).replace("|", "/")
            out += ["| " + " | ".join(b[1]) + " |", "|" + "---|" * len(b[1])]
            out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in b[2]] + [""]
    return "\n".join(out)


def render_txt(doc):
    out = []
    for b in doc:
        if b[0] == "h":
            out += [b[2], ("=" if b[1] == 1 else "-") * len(b[2]), ""]
        elif b[0] == "p":
            out += [b[1], ""]
        elif b[0] == "list":
            out += [f"  {i}" for i in b[1]] + [""]
        elif b[0] == "kv":       # aligned label / value / path columns, no header
            n = max(len(r) for r in b[1])
            rows = [r + [""] * (n - len(r)) for r in b[1]]
            w = [max(len(r[j]) for r in rows) for j in range(n)]
            out += ["  " + "  ".join(c.ljust(w[j]) for j, c in enumerate(r)).rstrip() for r in rows] + [""]
        elif b[0] == "table":
            rows = [b[1]] + [[str(c) for c in r] for r in b[2]]
            keep = [j for j in range(len(b[1])) if any(r[j] for r in rows[1:])]   # drop empty columns
            w = {j: max(len(r[j]) for r in rows) for j in keep}
            line = lambda r: "  ".join(r[j].ljust(w[j]) for j in keep).rstrip()
            out += [line(rows[0]), "  ".join("-" * w[j] for j in keep)] + [line(r) for r in rows[1:]] + [""]
    return "\n".join(out)


def collect(sub_dir, ended=()):
    """Write SUBMISSION_DIR/SUMMARY.{md,txt} (pass/fail) and RESULTS.{md,txt} (details) (atomically: jobs on different hosts may finish
    together). Two jobs collecting at the same moment can leave the older view; rerunning collect fixes it."""
    sub_dir = Path(sub_dir).resolve()
    for stem, doc in (("SUMMARY", summary_doc(sub_dir, ended)), ("RESULTS", collect_doc(sub_dir, ended))):
        for name, text in ((f"{stem}.md", render_md(doc)), (f"{stem}.txt", render_txt(doc))):
            tmp = sub_dir / f".{name}.{os.uname().nodename}.{os.getpid()}"
            tmp.write_text(text)
            os.replace(tmp, sub_dir / name)
    return sub_dir / "SUMMARY.txt"


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "summary":
            print(summary(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == "compare":
            print(compare(sys.argv[2], sys.argv[3]))
        elif len(sys.argv) in (3, 5) and sys.argv[1] == "collect" and sys.argv[3:4] in ([], ["--ended"]):
            print(f"Summary: {collect(sys.argv[2], sys.argv[4:])} (details: RESULTS.txt; both also as .md)")
        else:
            sys.exit(__doc__)
    except RunMissing as e:
        print(f"**RUN MISSING:** {e}")
        sys.exit(3)
