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
from paper_repro_gym import core, bundle, cli, scaffold, publish  # noqa: E402

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


def test_container_argv_locked_down():
    inp, scr, out = REPO / "inputs", REPO / ".gym/runs/R/scratch", REPO / ".gym/runs/R/output"
    argv = core.container_argv("img", inp, scr, out, ["python", "; rm -rf /"])
    check("argv[0] is the configured runtime", argv[0] == core.SANDBOX_POLICY["runtime"])
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


def test_preflight_classifies_boundary():
    pf = core.preflight("docker")
    check("docker preflight names the runtime", pf["runtime"] == "docker")
    # On a host whose user is in the docker group, docker is weak (root-equiv).
    if core._in_docker_group():
        check("docker+group classified weak", pf["boundary"] == "weak")
        check("weak boundary warns about root-equivalence",
              any("ROOT-EQUIVALENT" in w for w in pf["warnings"]))
    absent = core.preflight("definitely-not-a-runtime")
    check("absent runtime is unavailable", absent["available"] is False)
    check("absent runtime is not hardened", absent["boundary"] != "hardened")


def test_require_hardened_redline():
    """The enforceable redline: run refuses on a non-hardened boundary."""
    with tempfile.TemporaryDirectory() as t:
        inp = Path(t) / "inputs"; inp.mkdir()
        (inp / "x").write_text("noop")
        cmd = ["true"]
        ap = core.sign_approval(core.make_approval(
            paper_id="p", manifest_hash=core.manifest_of(inp), command=cmd,
            allowed_domains=[], max_seconds=5, max_output_mb=1, approved_by="op"), SECRET)
        # docker on this host is not hardened -> must refuse before running.
        try:
            core.run_container(approval=ap, secret=SECRET, image="alpine",
                               inputs_dir=inp, command=cmd, runs_dir=Path(t) / "runs",
                               require_hardened=True)
            raise AssertionError("FAIL: ran on a non-hardened boundary")
        except core.GateError as exc:
            check("require_hardened refuses a weak boundary", "not hardened" in str(exc))


def test_scaffold_from_dossier():
    proceed = {
        "identifier": "arxiv:2601.00001", "recommendation": "manual_review",
        "paper": {"title": "A Study", "canonical_id": "arxiv:2601.00001"},
        "required_artifacts": {"code": "claimed", "data": "claimed", "models": "not claimed", "verified": False},
        "compute_envelope": {"note": "7B; ~14 GiB of ~121 GiB unified"},
    }
    with tempfile.TemporaryDirectory() as t:
        dest = Path(t) / "exp"
        summ = scaffold.scaffold_from_dossier(proceed, dest)
        check("scaffold reports the paper id", summ["paper_id"] == "arxiv:2601.00001")
        for f in ["dossier.json", "experiment.json", "claims.json", "TODO.md", "inputs/PLACE_ARTIFACTS_HERE.md"]:
            check(f"scaffold wrote {f}", (dest / f).exists())
        claims = json.loads((dest / "claims.json").read_text())
        check("claims are NOT fabricated (value unset)", claims[0]["claimed_value"] is None)
        check("tolerance NOT fabricated", claims[0]["tolerance"] is None)
        exp = json.loads((dest / "experiment.json").read_text())
        check("command is a TODO template, not runnable", "TODO" in exp["command"][0])
        check("artifacts recorded as unverified", json.loads((dest / "dossier.json").read_text())["artifacts_verified"] is False)

    # no_go must be refused.
    try:
        scaffold.scaffold_from_dossier({"identifier": "x", "recommendation": "no_go"}, Path(t) / "n")
        raise AssertionError("FAIL: scaffolded a no_go dossier")
    except ValueError as exc:
        check("no_go dossier refused", "no_go" in str(exc))


def test_digest_pinning():
    check("tag is not digest-pinned", not core.is_digest_pinned("python:3.12-alpine"))
    check("digest ref is pinned", core.is_digest_pinned("python@sha256:" + "a" * 64))
    check("digest of absent image is None", core.image_digest("no-such-image:xyz") is None)


def test_scan_gate_blocks_run():
    """The enforced scan-gate: a .scan_block marker makes run refuse before it
    even looks at the approval."""
    with tempfile.TemporaryDirectory() as t:
        exp = Path(t)
        (exp / ".scan_block").write_text("findings present\n")
        try:
            cli._scan_gate(exp)
            raise AssertionError("FAIL: scan gate did not block")
        except core.GateError as exc:
            check("scan gate refuses a blocked experiment", "scan gate not cleared" in str(exc))
        (exp / ".scan_block").unlink()
        cli._scan_gate(exp)  # cleared -> no raise
        check("scan gate passes once cleared", True)


def test_bundle_records_digest_and_boundary():
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "b"
        run_rec = {"run_id": "R1", "image": "img@sha256:" + "b" * 64, "image_digest": "img@sha256:" + "b" * 64,
                   "digest_pinned": True, "boundary": "hardened", "command": ["x"],
                   "artifact_manifest_hash": "h", "sandbox_policy_hash": core.policy_hash(),
                   "sandbox_policy": core.SANDBOX_POLICY, "wall_seconds": 1, "output_bytes": 1,
                   "killed_on_timeout": False, "outcome": "COMPLETED", "stdout_tail": "",
                   "stderr_tail": "", "finished_at": "2026-07-24T00:00:00Z"}
        bundle.build_bundle(out_dir=out, dossier={"paper_id": "p"}, claims=[],
                            observed={}, run_record=run_rec, license_text="MIT", citation_cff="cff")
        man = json.loads((out / "experiment_manifest.json").read_text())
        check("manifest records image digest", man["image_digest"].startswith("img@sha256:"))
        check("manifest records digest_pinned", man["digest_pinned"] is True)
        check("manifest records boundary", man["boundary"] == "hardened")


def _make_bundle(t: Path) -> Path:
    """A minimal completed bundle for publish tests."""
    b = t / "bundle"; b.mkdir(parents=True)
    (b / "claim_result_matrix.json").write_text(json.dumps({
        "paper_id": "arxiv:2601.1", "overall_verdict": "REPRODUCED",
        "claims": [{"description": "acc", "metric": "acc", "claimed_value": 0.9,
                    "observed_value": 0.9, "tolerance": 0.01, "tolerance_kind": "abs",
                    "verdict": "REPRODUCED"}]}), encoding="utf-8")
    for f in ("experiment_manifest.json", "provenance.json", "summary.json",
              "REPRODUCIBILITY.md", "CITATION.cff", "LICENSE"):
        (b / f).write_text("{}" if f.endswith(".json") else "x", encoding="utf-8")
    (b / "logs").mkdir(); (b / "logs" / "run.json").write_text("{}", encoding="utf-8")
    return b


def test_publish_evidence_only():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        summ = publish.build_publish_repo(
            bundle_dir=_make_bundle(t), dest=t / "repo", paper_id="arxiv:2601.1",
            paper_url="https://arxiv.org/abs/2601.1", gym_url="https://example/gym")
        dest = t / "repo"
        check("verdict carried", summ["verdict"] == "REPRODUCED")
        for f in ["README.md", "ACQUISITION.md", "claim_result_matrix.json", "LICENSE", ".gitignore"]:
            check(f"repo has {f}", (dest / f).exists())
        check("README states the verdict", "REPRODUCED" in (dest / "README.md").read_text())
        check("acquisition says NOT redistributed", "not" in (dest / "ACQUISITION.md").read_text().lower()
              and "redistribute" in (dest / "ACQUISITION.md").read_text().lower())
        # The paper's artifacts are gitignored so they can never be committed.
        check("inputs/ is gitignored", "inputs/" in (dest / ".gitignore").read_text())


def test_publish_refuses_secrets_and_pii():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        b = _make_bundle(t)
        # A real email leaks into an evidence file.
        (b / "REPRODUCIBILITY.md").write_text("contact: someone@gmail.com\n", encoding="utf-8")
        try:
            publish.build_publish_repo(bundle_dir=b, dest=t / "r1", paper_id="p",
                                       paper_url="u", gym_url="g")
            raise AssertionError("FAIL: published a real email")
        except publish.PublishBlocked as exc:
            check("real email blocks publish", "gmail.com" in str(exc))

        # A token leaks.
        b2 = _make_bundle(t / "x")
        (b2 / "summary.json").write_text('{"t":"ghp_' + "a" * 36 + '"}', encoding="utf-8")
        try:
            publish.build_publish_repo(bundle_dir=b2, dest=t / "r2", paper_id="p",
                                       paper_url="u", gym_url="g")
            raise AssertionError("FAIL: published a token")
        except publish.PublishBlocked as exc:
            check("token blocks publish", "ghp_" in str(exc))

        # A GitHub no-reply email is allowed (not flagged as PII).
        b3 = _make_bundle(t / "y")
        (b3 / "CITATION.cff").write_text("email: 12345+user@users.noreply.github.com\n", encoding="utf-8")
        summ = publish.build_publish_repo(bundle_dir=b3, dest=t / "r3", paper_id="p",
                                          paper_url="u", gym_url="g")
        check("no-reply email is allowed", summ["verdict"] == "REPRODUCED")

        # A host path in the run log is REDACTED (username never leaks), and the
        # scan passes afterwards rather than blocking on evidence.
        b4 = _make_bundle(t / "z")
        host_path = "/home/" + "alice" + "/repro/inputs:/inputs:ro"  # neutral fixture, not this box
        (b4 / "logs" / "run.json").write_text('{"argv": ["-v", "' + host_path + '"]}', encoding="utf-8")
        summ = publish.build_publish_repo(bundle_dir=b4, dest=t / "r4", paper_id="p",
                                          paper_url="u", gym_url="g")
        run = (t / "r4" / "logs" / "run.json").read_text()
        check("home path redacted from evidence", "/home/alice" not in run and "<HOME>" in run)
        check("relative structure kept as evidence", "repro/inputs:/inputs:ro" in run)


def test_scaffold_selects_from_packet():
    packet = {"dossiers": [
        {"dossier_id": "ARC-1", "identifier": "a", "recommendation": "manual_review", "paper": {"title": "A"}},
        {"dossier_id": "ARC-2", "identifier": "b", "recommendation": "proceed", "paper": {"title": "B"}},
    ]}
    check("picks by id", scaffold.select_dossier(packet, "ARC-2")["identifier"] == "b")
    try:
        scaffold.select_dossier(packet, None)
        raise AssertionError("FAIL: ambiguous packet not rejected")
    except ValueError:
        check("ambiguous packet requires an id (no silent first-match)", True)


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
             test_scanner, test_container_argv_locked_down, test_preflight_classifies_boundary,
             test_require_hardened_redline, test_scaffold_from_dossier,
             test_digest_pinning, test_scan_gate_blocks_run,
             test_bundle_records_digest_and_boundary,
             test_publish_evidence_only, test_publish_refuses_secrets_and_pii,
             test_scaffold_selects_from_packet, test_claim_evaluation,
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
