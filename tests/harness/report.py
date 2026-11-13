#!/usr/bin/env python3
"""Markdown reports from the suite's JUnit XML (standard library only).

  report.py summary RUN_DIR                    one env: counts, failures, MPI table
  report.py compare CONTROL_DIR CANDIDATE_DIR  release vs candidate: regressions first

RUN_DIR is an OUTDIR written by harness/run.sh (junit.xml + fingerprint.json).
"""
import json
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
                          "props": props}
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


def compare(control_dir, candidate_dir):
    ctl, ctl_fp = load(control_dir)
    cand, cand_fp = load(candidate_dir)
    bad = ("failed", "error")
    groups = {"Regressions (fail only in candidate)": [],
              "Missing in candidate (test ran in control, absent now: package dropped?)": [],
              "Newly skipped (passed in control, skipped in candidate)": [],
              "Pre-existing (fail in both)": [],
              "Fixed (fail only in control)": [],
              "New tests failing (only in candidate)": []}
    for k in sorted(set(ctl) | set(cand)):
        a, b = ctl.get(k), cand.get(k)
        if a is None:
            if b["status"] in bad:
                groups["New tests failing (only in candidate)"].append((k, b["detail"]))
        elif b is None:
            if a["status"] != "skipped":
                groups["Missing in candidate (test ran in control, absent now: package dropped?)"].append(
                    (k, f"control: {a['status']}"))
        elif b["status"] in bad and a["status"] not in bad:
            groups["Regressions (fail only in candidate)"].append((k, b["detail"]))
        elif b["status"] in bad and a["status"] in bad:
            groups["Pre-existing (fail in both)"].append((k, b["detail"]))
        elif a["status"] in bad and b["status"] not in bad:
            groups["Fixed (fail only in control)"].append((k, a["detail"]))
        elif a["status"] == "passed" and b["status"] == "skipped":
            groups["Newly skipped (passed in control, skipped in candidate)"].append((k, b["detail"]))
    lines = [f"## {env_name(cand_fp)} (candidate) vs {env_name(ctl_fp)} (control) on "
             f"{cand_fp.get('host', '?')}", ""]
    for title, items in groups.items():
        lines.append(f"**{title}: {len(items)}**")
        lines += [f"- `{short(k)}`: {d.replace('|', '/')}" for k, d in items]
        lines.append("")
    for name, cases in ((env_name(ctl_fp), ctl), (env_name(cand_fp), cand)):
        table = mpi_table(cases)
        if table:
            lines += [f"MPI runs, {name}:", ""] + table + [""]
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "summary":
            print(summary(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == "compare":
            print(compare(sys.argv[2], sys.argv[3]))
        else:
            sys.exit(__doc__)
    except RunMissing as e:
        print(f"**RUN MISSING:** {e}")
        sys.exit(3)
