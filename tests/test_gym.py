#!/usr/bin/env python3
"""Offline + live checks for paper-repro-gym.

Unit tests always run. The live containment canary runs only if docker + the
example image are present (skipped honestly otherwise). No third-party paper
code is ever executed — the canary runs the first-party hello_repro example.

Run: python3 tests/test_gym.py
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from paper_repro_gym import core, bundle, cli  # noqa: E402

SECRET = "unit-test-secret"


def check(label, cond):
    assert cond, f"FAIL: {label}"
    print(f"  ok  {label}")


# ── gate ──────────────────────────────────────────────────────────────────

def test_manifest_and_gate():
    with tempfile.TemporaryDirectory() as t:
        inp = Path(t) / "inputs"; inp.mkdir()
        (inp / "a.py").write_text("print(1)\n")
        h1 = core.manifest_of(inp)
        (inp / "a.py").write_text("print(2)\n")
        check("manifest changes with content", h1 != core.manifest_of(inp))

    ap = core.sign_approval(core.make_approval(
        paper_id="p", manifest_hash="abc", command=["python", "x.py"],
        allowed_domains=[], max_seconds=60, max_output_mb=10, approved_by="op"), SECRET)
    core.verify_approval(ap, SECRET, manifest_hash="abc", command=["python", "x.py"])
    check("valid approval verifies", True)

    for label, kw in [("wrong manifest refused", dict(manifest_hash="X", command=["python", "x.py"])),
                      ("wrong command refused", dict(manifest_hash="abc", command=["evil"]))]:
        try:
            core.verify_approval(ap, SECRET, **kw); raise AssertionError(f"FAIL: {label}")
        except core.GateError:
            check(label, True)
    try:
        core.verify_approval(ap, "wrong", manifest_hash="abc", command=["python", "x.py"])
        raise AssertionError("FAIL: wrong secret accepted")
    except core.GateError:
        check("wrong secret refused", True)


def test_policy_change_invalidates():
    ap = core.sign_approval(core.make_approval(
        paper_id="p", manifest_hash="abc", command=["true"], allowed_domains=[],
        max_seconds=1, max_output_mb=1, approved_by="op"), SECRET)
    saved = dict(core.SANDBOX_POLICY)
    core.SANDBOX_POLICY["memory"] = "64g"
    try:
        core.verify_approval(ap, SECRET, manifest_hash="abc", command=["true"])
        raise AssertionError("FAIL: loosened policy still accepted")
    except core.GateError:
        check("policy change invalidates approval", True)
    finally:
        core.SANDBOX_POLICY.clear(); core.SANDBOX_POLICY.update(saved)


# ── acquisition + scan ──────────────────────────────────────────────────────

def test_domain_allowlist():
    check("approved host ok", core.host_allowed("https://github.com/x", ["github.com"]))
    check("subdomain ok", core.host_allowed("https://raw.github.com/x", ["github.com"]))
    check("unapproved refused", not core.host_allowed("https://evil.com/x", ["github.com"]))
    check("lookalike refused", not core.host_allowed("https://notgithub.com/x", ["github.com"]))


def _tar(members, links=None, traversal=None):
    tmp = Path(tempfile.mkdtemp()) / "a.tar"
    with tarfile.open(tmp, "w") as tf:
        for n, d in members.items():
            i = tarfile.TarInfo(n); i.size = len(d); tf.addfile(i, io.BytesIO(d))
        for n, tgt in (links or {}).items():
            i = tarfile.TarInfo(n); i.type = tarfile.SYMTYPE; i.linkname = tgt; tf.addfile(i)
        for n in (traversal or []):
            i = tarfile.TarInfo(n); i.size = 0; tf.addfile(i, io.BytesIO(b""))
    return tmp


def test_scanner():
    tar = _tar({"m.pkl": b"x", "w.safetensors": b"x", "setup.py": b"x", "ok.py": b"y"},
               links={"evil": "/etc/passwd"}, traversal=["../escape"])
    blob = "\n".join(core.scan_tarball(tar))
    check("pickle flagged", "m.pkl" in blob)
    check("safetensors not flagged", "w.safetensors" not in blob)
    check("install hook flagged", "setup.py" in blob)
    check("symlink flagged", "evil" in blob and "passwd" in blob)
    check("traversal flagged", "escape" in blob)
    check("benign not flagged", "ok.py" not in blob)


# ── container argv ──────────────────────────────────────────────────────────

def _sources(argv):
    return [argv[i + 1].split(":")[0] for i, a in enumerate(argv) if a == "-v" and i + 1 < len(argv)]


def test_docker_argv_locked_down():
    inp, scr, out = REPO / "inputs", REPO / ".gym/runs/R/scratch", REPO / ".gym/runs/R/output"
    argv = core.docker_argv("img", inp, scr, out, ["python", "; rm -rf /"])
    for flag in ["--network=none", "--cap-drop=ALL", "--read-only", "no-new-privileges", "--user=65534:65534"]:
        check(f"has {flag}", flag in argv)
    check("memory capped", any(a.startswith("--memory=") for a in argv))
    check("inputs read-only", f"{inp}:/inputs:ro" in argv)
    srcs = set(_sources(argv))
    check("only 3 run-scoped mounts", srcs == {str(inp), str(scr), str(out)})
    check("home root never mounted", not any(s == str(Path.home()) for s in srcs))
    check("secrets never mounted", not any(s.endswith(".env") or "/.ssh" in s or "/secrets" in s for s in srcs))
    check("no docker socket", not any("docker.sock" in a for a in argv))
    check("command is one argv element", "; rm -rf /" in argv)


# ── bundle scoring ──────────────────────────────────────────────────────────

def test_claim_evaluation():
    c = {"id": "C1", "metric": "acc", "claimed_value": 0.9, "tolerance": 0.01, "tolerance_kind": "abs"}
    check("within tolerance -> REPRODUCED", bundle.evaluate_claim(c, 0.905)["verdict"] == "REPRODUCED")
    check("outside tolerance -> NOT_REPRODUCED", bundle.evaluate_claim(c, 0.7)["verdict"] == "NOT_REPRODUCED")
    check("missing observed -> INCONCLUSIVE (not a pass)", bundle.evaluate_claim(c, None)["verdict"] == "INCONCLUSIVE")
    rel = {"id": "C2", "metric": "x", "claimed_value": 100.0, "tolerance": 0.05, "tolerance_kind": "rel"}
    check("relative tolerance honoured", bundle.evaluate_claim(rel, 103.0)["verdict"] == "REPRODUCED")
    check("relative tolerance exceeded", bundle.evaluate_claim(rel, 120.0)["verdict"] == "NOT_REPRODUCED")


def test_bundle_build():
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "b"
        run_rec = {"run_id": "R1", "image": "img", "command": ["python", "x"],
                   "artifact_manifest_hash": "deadbeef", "sandbox_policy_hash": core.policy_hash(),
                   "sandbox_policy": core.SANDBOX_POLICY, "wall_seconds": 1.2, "output_bytes": 10,
                   "killed_on_timeout": False, "outcome": "COMPLETED", "stdout_tail": "ok",
                   "stderr_tail": "", "finished_at": "2026-07-23T00:00:00Z"}
        claims = [{"id": "C1", "description": "d", "section": "s", "metric": "pi_estimate",
                   "claimed_value": 3.14159, "tolerance": 0.001, "tolerance_kind": "abs"}]
        summ = bundle.build_bundle(out_dir=out, dossier={"paper_id": "p"}, claims=claims,
                                   observed={"pi_estimate": 3.14161},
                                   run_record=run_rec, license_text="MIT", citation_cff="cff")
        check("overall REPRODUCED", summ["overall_verdict"] == "REPRODUCED")
        for f in ["claim_result_matrix.json", "experiment_manifest.json", "provenance.json",
                  "REPRODUCIBILITY.md", "README.md", "LICENSE", "CITATION.cff", "summary.json"]:
            check(f"bundle has {f}", (out / f).exists())
        prov = json.loads((out / "provenance.json").read_text())
        check("provenance claims only L1", "L1" in prov["build_level_claimed"])


# ── live containment canary ─────────────────────────────────────────────────

def test_live_demo():
    if not shutil.which("docker"):
        print("  ~~  docker absent — live demo SKIPPED"); return
    img = "python:3.12-alpine"
    if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode != 0:
        print(f"  ~~  {img} not local — not pulling; live demo SKIPPED"); return
    with tempfile.TemporaryDirectory() as t:
        rc = cli.main(["--workdir", t, "demo"])
        check("demo exits 0", rc == 0)
        bundles = list((Path(t) / "bundles").glob("*/summary.json"))
        check("a bundle was produced", len(bundles) == 1)
        summ = json.loads(bundles[0].read_text())
        check("example reproduces within tolerance", summ["overall_verdict"] == "REPRODUCED")
        run = json.loads(sorted((Path(t) / "runs").glob("*/run.json"))[-1].read_text())
        check("run labelled containment not sandbox", "NOT a security sandbox" in run["containment_note"])


def main() -> int:
    tests = [test_manifest_and_gate, test_policy_change_invalidates, test_domain_allowlist,
             test_scanner, test_docker_argv_locked_down, test_claim_evaluation,
             test_bundle_build, test_live_demo]
    failed = 0
    for fn in tests:
        print(f"\n{fn.__name__}:")
        try:
            fn()
        except AssertionError as exc:
            print(f"  {exc}"); failed += 1
    print(f"\n{'FAILED' if failed else 'PASS'}: {len(tests) - failed}/{len(tests)} test groups")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
