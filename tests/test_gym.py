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
from paper_repro_gym import (core, bundle, cli, scaffold, publish, index as index_mod,  # noqa: E402
                             hf_publish, council as council_mod)

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
            paper_url="https://arxiv.org/abs/2601.1", gym_url="https://example/gym",
            citation={"authors": "Doe, Jane; Roe, Rick", "title": "A Great Paper",
                      "year": "2024", "venue": "Journal of Things", "doi": "10.1/xyz",
                      "reproducer": "kowshikgunda71"})
        dest = t / "repo"
        check("verdict carried", summ["verdict"] == "REPRODUCED")
        for f in ["README.md", "ACQUISITION.md", "claim_result_matrix.json", "LICENSE", ".gitignore"]:
            check(f"repo has {f}", (dest / f).exists())
        check("README states the verdict", "REPRODUCED" in (dest / "README.md").read_text())
        check("acquisition says NOT redistributed", "not" in (dest / "ACQUISITION.md").read_text().lower()
              and "redistribute" in (dest / "ACQUISITION.md").read_text().lower())
        # The paper's artifacts are gitignored so they can never be committed.
        check("inputs/ is gitignored", "inputs/" in (dest / ".gitignore").read_text())
        cff = (dest / "CITATION.cff").read_text()
        check("CITATION cites the original authors", "Doe, Jane" in cff and "Roe, Rick" in cff)
        check("CITATION references the original paper", "A Great Paper" in cff and "10.1/xyz" in cff)
        check("README credits the original authors", "Doe, Jane" in (dest / "README.md").read_text())
        check("README foregrounds honest results", "reported honestly" in (dest / "README.md").read_text())


def test_publish_replication_does_not_claim_authors_artifacts():
    """A replication re-implements the experiment from the paper's text; it never
    touches the authors' code. Describing it as 'running the authors' own
    artifacts' is a false provenance claim, so the wording must follow the kind."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        cite = {"authors": "Doe, Jane", "title": "A Great Paper", "reproducer": "someone"}
        rep = publish.build_publish_repo(
            bundle_dir=_make_bundle(t), dest=t / "repl", paper_id="arxiv:2601.1",
            paper_url="u", gym_url="g", citation=cite, kind="replication")
        text = (t / "repl" / "README.md").read_text()
        check("kind recorded", rep["kind"] == "replication")
        check("no false artifact-provenance claim", "authors' own artifacts" not in text)
        check("badged as a replication", "Results Replicated" in text)
        check("says the authors' code was not used", "without using the" in text)
        check("CITATION.cff says replication", "replication" in (t / "repl" / "CITATION.cff").read_text())

        # The default is unchanged, so existing reproductions keep their wording.
        publish.build_publish_repo(
            bundle_dir=_make_bundle(t / "b2"), dest=t / "repro", paper_id="arxiv:2601.1",
            paper_url="u", gym_url="g", citation=cite)
        check("reproduction wording preserved by default",
              "authors' own artifacts" in (t / "repro" / "README.md").read_text())

        try:
            publish.build_publish_repo(
                bundle_dir=_make_bundle(t / "b3"), dest=t / "bad", paper_id="p",
                paper_url="u", gym_url="g", kind="reimplementation")
            check("unknown kind rejected", False)
        except ValueError:
            check("unknown kind rejected", True)


def test_publish_refuses_secrets_and_pii():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        b = _make_bundle(t)
        # A real email leaks into an evidence file.
        (b / "REPRODUCIBILITY.md").write_text("contact: someone@example.com\n", encoding="utf-8")
        try:
            publish.build_publish_repo(bundle_dir=b, dest=t / "r1", paper_id="p",
                                       paper_url="u", gym_url="g")
            raise AssertionError("FAIL: published a real email")
        except publish.PublishBlocked as exc:
            check("real email blocks publish", "example.com" in str(exc))

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


def test_hf_dataset_card():
    check("arxiv id from paper_id", hf_publish.arxiv_id_from("arxiv:2601.12345", "") == "2601.12345")
    check("arxiv id from url", hf_publish.arxiv_id_from("x", "https://arxiv.org/abs/2601.99999") == "2601.99999")
    check("no arxiv id -> None", hf_publish.arxiv_id_from("doi:10.1/x", "https://doi.org/10.1/x") is None)
    # A DOI that happens to contain a YYMM.NNNNN-shaped substring must NOT match.
    check("DOI digits are not an arxiv id",
          hf_publish.arxiv_id_from("doi:10.1080/00031305.1973.10478966",
                                   "https://doi.org/10.1080/00031305.1973.10478966") is None)

    fm = hf_publish.dataset_card_frontmatter(
        {"title": "A Paper"}, "REPRODUCED", "2601.12345")
    check("frontmatter is YAML block", fm.startswith("---\n") and "\n---\n" in fm)
    check("license mit", "license: mit" in fm)
    check("arxiv tag present", "arxiv: 2601.12345" in fm)
    check("verdict tag present", "verdict-reproduced" in fm)

    with tempfile.TemporaryDirectory() as t:
        r = Path(t) / "README.md"
        r.write_text("# Reproduction\n\nbody\n")
        hf_publish.add_card_frontmatter(r, fm)
        check("card prepended", r.read_text().startswith("---\n"))
        hf_publish.add_card_frontmatter(r, fm)  # idempotent
        check("carding is idempotent", r.read_text().count("license: mit") == 1)


def test_index_bench():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        def repo(name, verdict, boundary, claims):
            d = root / name; d.mkdir()
            (d / "claim_result_matrix.json").write_text(json.dumps({
                "paper_id": name, "overall_verdict": verdict, "evaluated_at": "2026-07-24T00:00:00Z",
                "claims": [{"verdict": "REPRODUCED"}] * claims}), encoding="utf-8")
            (d / "experiment_manifest.json").write_text(json.dumps({"boundary": boundary}), encoding="utf-8")
        repo("repro-a", "REPRODUCED", "hardened", 3)
        repo("repro-b", "NOT_REPRODUCED", "hardened", 1)
        repo("repro-c", "REPRODUCED", "weak", 2)

        idx = index_mod.build_index(root)
        check("counts all three", idx["summary"]["total"] == 3)
        check("2 reproduced", idx["summary"]["REPRODUCED"] == 2)
        check("1 not reproduced", idx["summary"]["NOT_REPRODUCED"] == 1)
        check("records boundary", all(e["boundary"] in ("hardened", "weak") for e in idx["entries"]))
        md = index_mod.render_md(idx)
        check("markdown has a table row per repro", md.count("| repro-") == 3)
        check("markdown shows the verdict", "REPRODUCED" in md and "NOT_REPRODUCED" in md)
        check("empty dir is handled", index_mod.build_index(root / "nope")["summary"]["total"] == 0)


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


# ── adversarial council ─────────────────────────────────────────────────────

def _fake_ask(panelist_payload, judge_payload, fail_lens=None):
    """Stand in for the model call so the council's orchestration is testable
    offline. Returns (parsed, usage) exactly as council._ask does."""
    def ask(client, *, model, system, prompt, schema, effort, max_tokens):
        if schema is council_mod.JUDGE_SCHEMA:
            return dict(judge_payload), {"input_tokens": 10, "output_tokens": 20}
        if fail_lens and f"YOUR ASSIGNED ANGLE — {fail_lens}" in prompt:
            raise council_mod.CouncilError("simulated reviewer failure")
        return dict(panelist_payload), {"input_tokens": 1, "output_tokens": 2}
    return ask


_OBJ = {"severity": "major", "claim_id": "c1", "statement": "wrong split",
        "evidence": "claims.json", "falsifiable_check": "rerun on the test split"}
_JUDGE = {"steelman": "the tolerance was pre-registered", "credibility": "SOUND",
          "rulings": [{"lens": "metric-identity", "statement": "wrong split",
                       "ruling": "upheld", "reason": "the split is unstated"}],
          "rationale": "narrow but standing", "open_checks": ["rerun on the test split"]}


def test_council_cannot_change_the_verdict():
    """The load-bearing property: a council finding never rewrites the mechanical
    verdict, and never writes into the claim/result matrix."""
    with tempfile.TemporaryDirectory() as td:
        b = _make_bundle(Path(td))
        before = (b / "claim_result_matrix.json").read_text()
        real_ask, real_client = council_mod._ask, council_mod._client
        council_mod._ask = _fake_ask({"objections": [_OBJ], "no_objection_reason": ""}, _JUDGE)
        council_mod._client = lambda: object()
        try:
            rec = council_mod.run_council(b)
            council_mod.write_council(b, rec)
        finally:
            council_mod._ask, council_mod._client = real_ask, real_client

        check("matrix untouched by the council", (b / "claim_result_matrix.json").read_text() == before)
        check("mechanical verdict carried verbatim", rec["mechanical_verdict"] == "REPRODUCED")
        check("council record has no verdict field to write",
              "overall_verdict" not in rec and "verdict" not in rec)
        check("judge schema exposes no verdict field",
              "verdict" not in council_mod.JUDGE_SCHEMA["properties"])
        check("every panel lens reported", len(rec["panel"]) == len(council_mod.PANEL))
        check("objections aggregated across the panel",
              len(rec["objections"]) == len(council_mod.PANEL))
        check("upheld rulings surfaced", len(rec["upheld"]) == 1)
        check("council.json + COUNCIL.md written",
              (b / "council.json").exists() and (b / "COUNCIL.md").exists())
        check("review bound to the evidence it read",
              rec["evidence_sha256"] == council_mod.evidence_hash(b))
        check("a fresh review is not stale", council_mod.review_state(b)["stale"] is False)
        md = (b / "COUNCIL.md").read_text()
        check("md says the verdict is unchanged", "cannot alter it" in md)
        check("md carries the open checks", "rerun on the test split" in md)


def test_council_downgrades_when_a_reviewer_fails():
    """A panel that did not fully report must not read as a clean sweep."""
    with tempfile.TemporaryDirectory() as td:
        b = _make_bundle(Path(td))
        real_ask, real_client = council_mod._ask, council_mod._client
        council_mod._ask = _fake_ask({"objections": [], "no_objection_reason": "held up"},
                                     _JUDGE, fail_lens="result-leakage")
        council_mod._client = lambda: object()
        try:
            rec = council_mod.run_council(b)
        finally:
            council_mod._ask, council_mod._client = real_ask, real_client
        check("SOUND downgraded to QUALIFIED", rec["credibility"] == "QUALIFIED")
        check("the failed lens is named", "result-leakage" in rec["judge"]["rationale"])
        check("failure recorded on the panelist",
              any(p.get("error") for p in rec["panel"] if p["lens"] == "result-leakage"))


def test_council_refuses_non_bundle_and_flags_missing_code():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        try:
            council_mod.run_council(t)
            check("refuses a directory that is not a bundle", False)
        except council_mod.CouncilError as exc:
            check("refuses a directory that is not a bundle", "not a reproduction bundle" in str(exc))
        b = _make_bundle(t)
        ev = council_mod.gather_evidence(b)
        check("uncaptured harness code is stated, not silently omitted", "NOT CAPTURED" in ev)
        check("evidence includes the claim matrix", "claim_result_matrix.json" in ev)


def test_index_shows_review_state():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        b = _make_bundle(t)
        idx = index_mod.build_index(t)
        check("unreviewed bundle reads as not reviewed",
              "not reviewed" in index_mod.render_md(idx))
        (b / "council.json").write_text(json.dumps(
            {"credibility": "DISPUTED", "upheld": [{"lens": "x"}, {"lens": "y"}],
             "evidence_sha256": council_mod.evidence_hash(b)}), encoding="utf-8")
        idx = index_mod.build_index(t)
        check("credibility surfaced on the board", idx["entries"][0]["credibility"] == "DISPUTED")
        check("upheld count surfaced on the board", "DISPUTED (2)" in index_mod.render_md(idx))

        # The evidence changes after the review -> the old verdict must not stand.
        (b / "summary.json").write_text('{"overall_verdict": "REPRODUCED"}', encoding="utf-8")
        state = council_mod.review_state(b)
        check("changed evidence detected as stale", state["stale"] is True)
        idx = index_mod.build_index(t)
        check("stale review is not shown as credibility", idx["entries"][0]["credibility"] is None)
        check("stale review reads as stale, not as approval",
              "stale review" in index_mod.render_md(idx))


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
             test_hf_dataset_card, test_index_bench,
             test_scaffold_selects_from_packet, test_claim_evaluation,
             test_council_cannot_change_the_verdict,
             test_council_downgrades_when_a_reviewer_fails,
             test_council_refuses_non_bundle_and_flags_missing_code,
             test_index_shows_review_state,
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


def test_remote_refuses_to_upload_secrets():
    """Uploading a payload to third-party compute has the same disclosure
    consequence as publishing it, so it must hit the same scanner. The failure
    this pins: an API token riding along inside a harness or notebook."""
    from paper_repro_gym import remote

    calls = []
    prov = remote.get("kaggle", runner=lambda a: calls.append(a) or "successfully pushed")

    with tempfile.TemporaryDirectory() as td:
        clean = Path(td) / "clean"; clean.mkdir()
        (clean / "harness.py").write_text("print('hello')\n", encoding="utf-8")
        res = prov.submit(clean)
        check("clean payload submits", res["submitted"] and res["scanned"])
        check("push actually invoked", any("push" in c for c in calls))

        dirty = Path(td) / "dirty"; dirty.mkdir()
        (dirty / "nb.py").write_text("KEY = 'gh" + "p_" + "A" * 24 + "'\n", encoding="utf-8")
        before = len(calls)
        try:
            prov.submit(dirty)
            check("secret payload refused", False)
        except remote.RemoteBlocked:
            check("secret payload refused", True)
        check("no upload attempted after refusal", len(calls) == before)

    check("unknown provider rejected",
          _raises(lambda: remote.get("nope"), ValueError))


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def test_one_sided_claims_are_not_scored_two_sided():
    """Regression for a real defect in this tool's first published replication:
    the paper claimed 'improving by more than 0.3 percentage points' and it was
    registered two-sided, which invents an upper bound the paper never stated."""
    lower = {"id": "C2", "metric": "m", "claimed_value": 0.3,
             "tolerance": 0.05, "tolerance_kind": "lower_bound"}
    check("overshoot passes a lower bound", bundle.evaluate_claim(lower, 0.9)["verdict"] == "REPRODUCED")
    check("at the bound passes", bundle.evaluate_claim(lower, 0.3)["verdict"] == "REPRODUCED")
    check("undershoot beyond tolerance fails",
          bundle.evaluate_claim(lower, 0.1)["verdict"] == "NOT_REPRODUCED")
    # The exact defect: two-sided scoring rejects a clear satisfaction of the claim.
    two_sided = {**lower, "tolerance_kind": "abs", "tolerance": 0.3}
    check("two-sided would have wrongly rejected the overshoot",
          bundle.evaluate_claim(two_sided, 0.9)["verdict"] == "NOT_REPRODUCED")

    upper = {"id": "C6", "metric": "m", "claimed_value": 0.35,
             "tolerance": 0.05, "tolerance_kind": "upper_bound"}
    check("under an upper bound passes", bundle.evaluate_claim(upper, 0.1)["verdict"] == "REPRODUCED")
    check("over an upper bound fails", bundle.evaluate_claim(upper, 0.9)["verdict"] == "NOT_REPRODUCED")

    iv = {"id": "I1", "metric": "m", "claimed_value": [1.0, 4.0],
          "tolerance": 0.1, "tolerance_kind": "interval"}
    check("inside an interval passes", bundle.evaluate_claim(iv, 2.5)["verdict"] == "REPRODUCED")
    check("outside an interval fails", bundle.evaluate_claim(iv, 4.5)["verdict"] == "NOT_REPRODUCED")
    check("absence is still inconclusive",
          bundle.evaluate_claim(lower, None)["verdict"] == "INCONCLUSIVE")


def test_tolerance_policy_is_mechanical():
    """Tolerances must be a function of the paper, not of the reproducer. The
    objection this answers: 'you picked tolerances that made failures likely.'"""
    from paper_repro_gym import tolerance as T

    check("rounding floor from printed precision", T.rounding_floor("38") == 0.5)
    check("floor tracks decimals", T.rounding_floor("0.35") == 0.005)
    check("percent sign tolerated", T.rounding_floor("21.1%") == 0.05)

    # R1: the paper's own dispersion wins over the default.
    r1 = T.derive("38", reported_dispersion=12.0, dispersion_kind="min/max over 5 trials")
    check("R1 uses the paper's dispersion", r1["tolerance"] == 12.0 and r1["rule"] == "R1")

    # R3: no dispersion -> bench-wide constant, identical for every such claim.
    r3 = T.derive("2.51")
    check("R3 is the declared constant", abs(r3["tolerance"] - 0.251) < 1e-9 and r3["rule"] == "R3")

    # The floor always applies: never ask for precision the paper never printed.
    tight = T.derive("0.35", reported_dispersion=0.0001)
    check("floor raises an over-tight dispersion", tight["tolerance"] == 0.005)
    check("floor promotion is recorded", "R2" in tight["rule"])

    # R0: structural claims have no measurement noise; the empirical default
    # would pass almost any implementation, which defeats their purpose.
    r0 = T.derive("21.1", deterministic=True)
    check("structural claim gets the rounding interval", r0["tolerance"] == 0.05)
    check("structural rule recorded", r0["rule"] == "R0")
    check("structural is far tighter than the empirical default",
          r0["tolerance"] < T.derive("21.1")["tolerance"])

    # Hand-set tolerances stay legal but must be visible.
    findings = T.audit([{"id": "C1", "tolerance": 15.0},
                        {"id": "C2", "tolerance": 0.3, "tolerance_rule": "R3"}])
    check("hand-set tolerance is flagged", len(findings) == 1 and "C1" in findings[0])


def test_underpowered_is_not_the_same_as_failed():
    """A claim the sample cannot resolve is unfalsifiable, not refuted. Calling it
    NOT_REPRODUCED asserts evidence against a paper that the data lacks."""
    from paper_repro_gym import tolerance as T
    # LTH C1 at n=5: 2-SE band spans both the claimed 38% and ~0%.
    check("C1 is underpowered, not refuted",
          T.is_underpowered(observed_mean=21.4, tolerance=21.1, claimed=38.0))
    # LTH C5: band [0.29, 0.48] excludes both the claimed 0.5 and 0 -> a real result.
    check("a precise miss is a genuine failure, not underpower",
          not T.is_underpowered(observed_mean=0.388, tolerance=0.0945, claimed=0.5))
    check("a precise hit is not underpowered",
          not T.is_underpowered(observed_mean=0.5, tolerance=0.02, claimed=0.5))


def test_figures_are_generated_from_measured_values_only():
    """Figures in a replication repo are evidence. This pins that every rung the
    run produced appears, and that nothing is invented between them."""
    try:
        from paper_repro_gym import figures
    except ModuleNotFoundError:
        print("  skip figures test: matplotlib not installed (optional extra)")
        return
    runs = {s: {"_arch": "x", "_params": 100, "_config": {"seed": s, "device": "cpu", "wall_seconds": 60},
                "_levels": [{"round": k, "pm": 100 * (0.8 ** k),
                             "ticket": {"test_acc": 0.90 - 0.001 * k, "early_stop_iter": 100}}
                            for k in range(6)]} for s in (0, 1)}
    runs[0]["_levels"][3]["random"] = {"test_acc": 0.80, "early_stop_iter": 300}
    with tempfile.TemporaryDirectory() as td:
        out = figures.build(runs, Path(td), label="unit")
        for key in ("ladder", "ladder_delta", "reinit", "markdown"):
            check(f"produced {key}", key in out and Path(out[key]).exists())
        md = (Path(td) / "FIGURES.md").read_text()
        check("every rung is in the table", all(f"{100*(0.8**k):.2f}" in md for k in range(6)))
        check("seed count reported", "2 seeds" in md)
        check("pipeline diagram embedded", "mermaid" in md)


def test_figures_never_merge_architectures():
    """Seed numbers repeat across architectures. A flat {seed: run} map drops
    every colliding run AND still renders a plausible-looking figure, which is
    the dangerous failure: silently fewer runs than the caption claims."""
    try:
        from paper_repro_gym import figures  # noqa: F401
    except ModuleNotFoundError:
        print("  skip: matplotlib not installed (optional extra)")
        return
    import subprocess
    def mk(arch, seed):
        return {"_arch": arch, "_params": 1, "_config": {"seed": seed, "device": "cpu", "wall_seconds": 1},
                "_levels": [{"round": k, "pm": 100 * (0.8 ** k),
                             "ticket": {"test_acc": 0.5, "early_stop_iter": 1}} for k in range(3)]}
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in"; src.mkdir()
        for arch in ("convA", "convB"):
            for seed in (0, 1):                       # seeds collide across arches
                (src / f"metrics-{arch}-{seed}.json").write_text(json.dumps(mk(arch, seed)))
        out = Path(td) / "out"
        r = subprocess.run([sys.executable, "-m", "paper_repro_gym.cli", "figures",
                            str(src), str(out)], capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(REPO / "src")})
        check("figures cli succeeded", r.returncode == 0)
        check("one directory per architecture",
              (out / "convA").is_dir() and (out / "convB").is_dir())
        for arch in ("convA", "convB"):
            md = (out / arch / "FIGURES.md").read_text()
            check(f"{arch} kept both of its seeds", "2 seeds" in md)


def test_consistency_auditor_on_two_real_papers():
    """Zero-compute internal-consistency checks, pinned against the two real
    cases that motivated the module. They point in opposite directions, which is
    the whole value: one paper's printed count contradicts its own architecture,
    the other's recovers a detail the text omits."""
    from paper_repro_gym import consistency as K

    # --- Zhang et al. 2017, Table 1: SELF-REPAIRING -------------------------
    # The paper never states its CIFAR-10 crop. Printed counts recover it.
    full = K.mlp_params(32 * 32 * 3, 1, 512, 10)
    crop = K.mlp_params(28 * 28 * 3, 1, 512, 10)
    check("full-image reading does not match Table 1", full == 1_578_506)
    check("28x28 reading matches Table 1 exactly", crop == 1_209_866)
    f = K.check("MLP 1x512", printed=1_209_866, computed=full,
                alternatives={"a 28x28x3 centre crop": crop})
    check("Zhang classified self-repairing", f.verdict == K.SELF_REPAIRING)
    check("Zhang is not reported as a defect", not f.is_defect)
    check("the recovered reading is named", "28x28x3" in str(f))
    # And the crop is recoverable without being told the answer:
    check("input dim solved from the printed count",
          K.solve_mlp_input_dim(1_209_866, 1, 512, 10) == 2352)
    check("depth-3 row recovers the same crop",
          K.solve_mlp_input_dim(1_735_178, 3, 512, 10) == 2352)

    # --- Frankle & Carbin, Figure 2: CONTRADICTORY --------------------------
    # Conv-2 and Conv-4 match exactly; Conv-6 cannot be reconciled.
    # bias=False: Figure 2 counts WEIGHTS, which is also what the pruning
    # harness treats as prunable. Counting biases shifts Conv-2 by exactly 650,
    # which would silently break an exact-match audit.
    cp = lambda spec: K.conv_params(spec, 3, [256, 256], 10, spatial=32, bias=False)
    conv2, conv4 = cp([[64, 64]]), cp([[64, 64], [128, 128]])
    conv6 = cp([[64, 64], [128, 128], [256, 256]])
    check("Conv-2 matches its printed 4.3M", abs(conv2 - 4_300_992) == 0)
    check("Conv-4 matches its printed 2.4M", abs(conv4 - 2_425_024) == 0)
    check("Conv-6 computes to 2,261,184", conv6 == 2_261_184)
    g = K.check("Conv-6", printed=1_700_000, computed=conv6, tolerance=50_000,
                alternatives={"no padding": K.conv_params(
                    [[64, 64], [128, 128], [256, 256]], 3, [256, 256], 10,
                    spatial=26, bias=False)})
    check("LTH Conv-6 classified contradictory", g.verdict == K.CONTRADICTORY)
    check("contradictory counts as a defect", g.is_defect)

    # A matching count must not be dressed up as a finding.
    m = K.check("Conv-2", printed=4_300_992, computed=conv2)
    check("exact match reported as MATCH", m.verdict == K.MATCH and not m.is_defect)

    txt = K.report([f, g, m])
    check("report ranks the contradiction first", txt.index("Conv-6") < txt.index("MLP 1x512"))
    check("report tallies every verdict", "1 match, 1 self-repairing, 1 contradictory" in txt)


def test_underpowered_claims_score_inconclusive_not_failed():
    """The capability must reach the VERDICT, not just exist as a library. LTH's
    C1 is the worked case: a 2-SE band 42 points wide against a 38-point claim."""
    c1 = {"id": "C1", "metric": "early_stop_reduction_pct", "claimed_value": 38.0,
          "tolerance": 21.1, "tolerance_kind": "abs", "null_value": 0.0}
    r = bundle.evaluate_claim(c1, 21.4)
    check("unresolvable claim is INCONCLUSIVE", r["verdict"] == "INCONCLUSIVE")
    check("flagged as underpowered", r.get("underpowered") is True)
    check("reason says neither corroborated nor refuted",
          "neither corroborated nor refuted" in r.get("reason", ""))
    # The dangerous direction: a band wider than the effect makes the claim
    # SPURIOUSLY PASS. It must be caught there too, not only on failures.
    check("it would otherwise have falsely passed", r.get("would_have_scored") == "REPRODUCED")

    # A precise miss is still a failure -- underpower must not become an excuse.
    precise = {**c1, "claimed_value": 0.5, "tolerance": 0.0945, "null_value": 0.0}
    check("a precise miss stays NOT_REPRODUCED",
          bundle.evaluate_claim(precise, 0.388)["verdict"] == "NOT_REPRODUCED")

    # Without a declared null there is no power question and the wide band
    # silently passes the claim -- which is exactly the false corroboration the
    # null_value declaration exists to catch. Pinned so the difference is visible.
    check("without null_value the same data FALSELY passes",
          bundle.evaluate_claim({k: v for k, v in c1.items() if k != "null_value"},
                                21.4)["verdict"] == "REPRODUCED")
    # An exact hit on an unfalsifiable claim is still unfalsifiable -- it is
    # inside a band that would have swallowed almost any value.
    check("an exact hit on an unresolvable claim is still INCONCLUSIVE",
          bundle.evaluate_claim(c1, 38.0)["verdict"] == "INCONCLUSIVE")
    # A well-powered pass is untouched.
    tight = {**c1, "tolerance": 2.0}
    check("a well-powered pass stays REPRODUCED",
          bundle.evaluate_claim(tight, 38.0)["verdict"] == "REPRODUCED")
