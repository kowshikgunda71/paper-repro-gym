"""paper-repro-gym CLI — the workbench that drives a reproduction end to end:
review -> sign (A1 gate) -> run (containerized) -> bundle (evidence).

Nothing runs without a signed A1 approval. The signing secret is read from
$GYM_APPROVAL_SECRET and never stored -- whoever holds it authorizes runs.

An experiment directory contains:
  experiment.json  {image, command, allowed_domains?, max_seconds, max_output_mb}
  dossier.json     {paper_id, recommendation, ...}
  claims.json      [{id, description, section, metric, claimed_value, tolerance, tolerance_kind}]
  inputs/          the code/data to run; the run writes /output/metrics.json
"""

from __future__ import annotations

import argparse
import json
import os
import secrets as _secrets
import shutil
import sys
import tarfile
from pathlib import Path

from . import core, bundle, scaffold, publish, index as index_mod, hf_publish, council

SCAN_BLOCK = ".scan_block"
GYM_URL = "https://github.com/kowshikgunda71/paper-repro-gym"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _asset(name: str) -> Path:
    """A file that lives in the repo checkout, not the package: LICENSE and
    CITATION.cff (copied into every bundle) and examples/ (the demo).

    Present for a source checkout and for `pip install -e .`; absent for a
    non-editable install, which is why the install docs say editable. Fails
    loudly here rather than writing a bundle with a missing LICENSE."""
    p = _REPO_ROOT / name
    if not p.exists():
        raise core.GateError(
            f"{name} not found at {p}. paper-repro-gym reads it from the repo "
            f"checkout, so install it editable (`pip install -e .`) or run from a "
            f"clone; a non-editable install cannot find its own LICENSE to stamp "
            f"into bundles.")
    return p


def _load(exp: Path, name: str) -> dict | list:
    return json.loads((exp / name).read_text(encoding="utf-8"))


def _secret() -> str:
    s = os.environ.get("GYM_APPROVAL_SECRET")
    if not s:
        raise core.GateError(
            "GYM_APPROVAL_SECRET is not set. The A1 gate needs it to sign/verify "
            "an approval — set it in the environment of whoever authorizes runs.")
    return s


def cmd_check(_args: argparse.Namespace) -> int:
    print(json.dumps({
        "policy_hash": core.policy_hash(),
        "sandbox_policy": core.SANDBOX_POLICY,
        "note": "containment, not a sandbox; runs only via a signed A1 approval",
    }, indent=2))
    return 0


def cmd_preflight(_args: argparse.Namespace) -> int:
    """Report the boundary strength of the current runtime. Exit non-zero when
    it is not hardened, so CI/scripts can gate on it."""
    pf = core.preflight()
    print(json.dumps(pf, indent=2))
    if pf["boundary"] != "hardened":
        print(f"\nboundary is '{pf['boundary']}', NOT hardened. Untrusted artifacts "
              f"should be run with --require-hardened (which will refuse here) or on "
              f"rootless podman / a disposable VM. See docs/PODMAN_UPGRADE.md.",
              file=sys.stderr)
        return 1
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.dossier).read_text(encoding="utf-8"))
    dossier = scaffold.select_dossier(payload, args.id)
    summary = scaffold.scaffold_from_dossier(dossier, Path(args.dest).resolve())
    print(json.dumps(summary, indent=2))
    print(f"\nNext: complete claims.json + experiment.json + inputs/ in {args.dest}, "
          f"then `gym approve {args.dest} && gym sign {args.dest} && gym run {args.dest}`. "
          f"See {Path(args.dest) / 'TODO.md'}.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Build an UNSIGNED approval for human review, printing what it binds."""
    exp = Path(args.experiment).resolve()
    spec = _load(exp, "experiment.json")
    dossier = _load(exp, "dossier.json")
    manifest_hash = core.manifest_of(exp / "inputs")
    approval = core.make_approval(
        paper_id=dossier.get("paper_id") or dossier.get("identifier") or "unknown",
        manifest_hash=manifest_hash, command=spec["command"],
        allowed_domains=spec.get("allowed_domains", []),
        max_seconds=int(spec.get("max_seconds", 900)),
        max_output_mb=int(spec.get("max_output_mb", 50)),
        approved_by=args.approved_by)
    (exp / "approval.json").write_text(json.dumps(approval, indent=2) + "\n", encoding="utf-8")
    print(f"Unsigned A1 approval written to {exp / 'approval.json'}. Review it, then `gym sign`.")
    print(f"  binds manifest_hash={manifest_hash[:16]}… command={spec['command']} policy={core.policy_hash()[:16]}…")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    exp = Path(args.experiment).resolve()
    approval = json.loads((exp / "approval.json").read_text(encoding="utf-8"))
    core.sign_approval(approval, _secret())
    (exp / "approval.signed.json").write_text(json.dumps(approval, indent=2) + "\n", encoding="utf-8")
    print(f"Signed approval written to {exp / 'approval.signed.json'}.")
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    """Print the digest-pinned reference for an image, to paste into
    experiment.json for a bit-reproducible run."""
    d = core.image_digest(args.image)
    if not d:
        print(f"could not resolve a digest for {args.image!r} — pull it first "
              f"({core.SANDBOX_POLICY['runtime']} pull {args.image}).", file=sys.stderr)
        return 1
    print(d)
    return 0


def _scan_gate(exp: Path) -> None:
    """The enforced scan-gate: `gym acquire` writes .scan_block when a downloaded
    artifact tripped the scanner; `run` refuses until it is cleared."""
    if (exp / SCAN_BLOCK).exists():
        raise core.GateError(
            f"scan gate not cleared: `gym acquire` found issues in this experiment's "
            f"artifacts (see acquisition.json). Resolve them, or re-acquire with "
            f"--allow-findings if you have reviewed and accept them.")


def cmd_acquire(args: argparse.Namespace) -> int:
    """Download the experiment's declared artifacts into inputs/, quarantined,
    checksum-verified, and scanned. A scan finding BLOCKS the run unless
    explicitly overridden with --allow-findings."""
    exp = Path(args.experiment).resolve()
    spec = _load(exp, "experiment.json")
    artifacts = spec.get("artifacts", [])
    if not artifacts:
        print("no `artifacts` declared in experiment.json — nothing to acquire.")
        (exp / SCAN_BLOCK).unlink(missing_ok=True)
        return 0

    inputs = exp / "inputs"; inputs.mkdir(exist_ok=True)
    quarantine = exp / ".quarantine"
    domains = spec.get("allowed_domains", [])
    report: dict = {"artifacts": [], "findings": []}

    for a in artifacts:
        path = core.acquire(a["url"], domains, quarantine, a.get("sha256"))
        findings = core.scan_tarball(path) if tarfile.is_tarfile(path) else []
        dest = inputs / (a.get("dest") or path.name.split("-", 1)[-1])
        shutil.copy2(path, dest)
        report["artifacts"].append({
            "url": a["url"], "sha256": core.sha256_file(dest),
            "dest": str(dest.relative_to(exp)), "findings": findings})
        report["findings"].extend(findings)

    (exp / "acquisition.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if report["findings"] and not args.allow_findings:
        (exp / SCAN_BLOCK).write_text("scan findings present; review acquisition.json, "
                                      "then re-run acquire with --allow-findings to override\n")
        print(f"BLOCKED: {len(report['findings'])} scan finding(s) — the run is now gated. "
              f"See {exp / 'acquisition.json'}:", file=sys.stderr)
        for f in report["findings"][:10]:
            print(f"  - {f}", file=sys.stderr)
        return 3

    (exp / SCAN_BLOCK).unlink(missing_ok=True)
    print(json.dumps({"acquired": len(report["artifacts"]),
                      "findings": len(report["findings"]),
                      "gate": "clear"}, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    exp = Path(args.experiment).resolve()
    _scan_gate(exp)
    spec = _load(exp, "experiment.json")
    approval = json.loads((exp / "approval.signed.json").read_text(encoding="utf-8"))
    workdir = Path(args.workdir).resolve()
    rec = core.run_container(
        approval=approval, secret=_secret(), image=spec["image"],
        inputs_dir=exp / "inputs", command=spec["command"], runs_dir=workdir / "runs",
        require_hardened=getattr(args, "require_hardened", False))
    print(f"run {rec['run_id']}: outcome={rec['outcome']} exit={rec['exit_code']} "
          f"wall={rec['wall_seconds']}s -> {workdir / 'runs' / rec['run_id'] / 'run.json'}")
    return 0 if rec["outcome"] in ("COMPLETED", "FAILED_SAFELY") else 1


def _observed_from_run(workdir: Path, run_id: str) -> dict:
    """Read /output/metrics.json the run produced. Missing -> empty (claims
    then evaluate INCONCLUSIVE, never a false pass)."""
    mp = workdir / "runs" / run_id / "output" / "metrics.json"
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_bundle(args: argparse.Namespace) -> int:
    exp = Path(args.experiment).resolve()
    workdir = Path(args.workdir).resolve()
    dossier = _load(exp, "dossier.json")
    claims = _load(exp, "claims.json")
    run_rec = json.loads((workdir / "runs" / args.run_id / "run.json").read_text(encoding="utf-8"))
    observed = _observed_from_run(workdir, args.run_id)
    summary = bundle.build_bundle(
        out_dir=workdir / "bundles" / args.run_id, dossier=dossier, claims=claims,
        observed=observed, run_record=run_rec,
        license_text=_asset("LICENSE").read_text(encoding="utf-8"),
        citation_cff=_asset("CITATION.cff").read_text(encoding="utf-8"))
    # Capture the reproduction spec + the reproducer's harness code into the
    # bundle so a published reproduction shows exactly what was run.
    code_dir = bundle.capture_reproduction_code(exp, workdir / "bundles" / args.run_id)
    summary["code_captured"] = bool(code_dir)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_figures(args: argparse.Namespace) -> int:
    """Render figures + tables from a bundle's or a directory's metrics.

    CPU only, by design: accelerator quota buys training, not plotting."""
    from . import figures
    src, dest = Path(args.source), Path(args.dest)
    runs = {}
    for f in sorted(src.rglob("metrics*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "_levels" in d:
            runs[d.get("_config", {}).get("seed", len(runs))] = d
    if not runs:
        print(f"no metrics*.json with _levels under {src}", file=sys.stderr)
        return 1
    matrix = None
    m = src / "claim_result_matrix.json"
    if m.exists():
        matrix = json.loads(m.read_text(encoding="utf-8"))
    print(json.dumps(figures.build(runs, dest, matrix=matrix, label=args.label), indent=2))
    return 0


def cmd_remote(args: argparse.Namespace) -> int:
    """Run an experiment on external compute (Kaggle, ...) when it does not fit
    the gym's 4-CPU / no-GPU sandbox.

    `preflight` and `submit` scan the payload with the same scanner that gates
    `gym publish`: handing code to someone else's compute discloses it just as
    publishing does. Results come home via `gym import-run`, which records
    `boundary: external:<provider>` rather than claiming containment."""
    from . import remote
    payload = Path(args.path).resolve() if args.path else None

    if args.action == "preflight":
        findings = remote.preflight(payload)
        print(json.dumps({"path": str(payload), "safe_to_upload": not findings,
                          "findings": findings[:20]}, indent=2))
        return 1 if findings else 0

    prov = remote.get(args.provider)
    try:
        if args.action == "submit":
            out = prov.submit(payload)
        elif args.action == "status":
            out = prov.status(args.ref)
        else:
            out = prov.fetch(args.ref, Path(args.dest or "."))
    except remote.RemoteBlocked as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


def cmd_import_run(args: argparse.Namespace) -> int:
    """Score an externally-produced result (e.g. from Colab/Kaggle/an HPC job)
    against this experiment's pre-registered claims, and build a bundle. Use
    this when the heavy run happened off-box: bring back only metrics.json.

    Honest by construction: the boundary is recorded as `external:<where>`, not
    a gym-contained run, so the bundle never overstates the isolation."""
    from datetime import datetime, timezone
    exp = Path(args.experiment).resolve()
    workdir = Path(args.workdir).resolve()
    claims = _load(exp, "claims.json")
    dossier = _load(exp, "dossier.json")
    observed = json.loads(Path(args.metrics).read_text(encoding="utf-8"))

    run_id = "external-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_hash = core.manifest_of(exp / "inputs") if (exp / "inputs").is_dir() else "external"
    run_rec = {
        "run_id": run_id, "paper_id": dossier.get("paper_id"),
        "artifact_manifest_hash": manifest_hash, "sandbox_policy_hash": None,
        "sandbox_policy": {"runtime": "external"}, "image": args.image or "external",
        "image_digest": None, "digest_pinned": False,
        "command": (args.command.split() if args.command else []),
        "boundary": f"external:{args.ran_on}", "exit_code": 0, "killed_on_timeout": False,
        "wall_seconds": None, "stdout_tail": "", "stderr_tail": "", "output_bytes": 0,
        "output_over_cap": False, "outcome": "COMPLETED",
        "containment_note": f"run externally on {args.ran_on}; NOT gym-contained "
                            "(the external environment was the sandbox)",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    run_dir = workdir / "runs" / run_id
    (run_dir / "output").mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(run_rec, indent=2), encoding="utf-8")
    (run_dir / "output" / "metrics.json").write_text(json.dumps(observed), encoding="utf-8")

    summary = bundle.build_bundle(
        out_dir=workdir / "bundles" / run_id, dossier=dossier, claims=claims,
        observed=observed, run_record=run_rec,
        license_text=_asset("LICENSE").read_text(encoding="utf-8"),
        citation_cff=_asset("CITATION.cff").read_text(encoding="utf-8"))
    bundle.capture_reproduction_code(exp, workdir / "bundles" / run_id)
    print(json.dumps({**summary, "run_id": run_id, "boundary": run_rec["boundary"]}, indent=2))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Stage a completed bundle into its OWN standalone reproduction repo
    (evidence only). Hard-refuses on any secret/PII. Never pushes — prints the
    exact gh command for you to run after review."""
    dest = Path(args.dest).resolve()
    citation = {
        "authors": args.authors, "title": args.paper_title, "year": args.paper_year,
        "venue": args.paper_venue, "doi": args.paper_doi, "reproducer": args.author_name,
    }
    try:
        summary = publish.build_publish_repo(
            bundle_dir=Path(args.bundle).resolve(), dest=dest,
            paper_id=args.paper_id, paper_url=args.paper_url, gym_url=GYM_URL,
            citation=citation, include_code=not args.no_code,
            kind="replication" if args.replication else "reproduction")
    except publish.PublishBlocked as exc:
        print(f"PUBLISH REFUSED — {exc}", file=sys.stderr)
        return 2

    # Local git only: init + commit under a no-reply author. NO push.
    import subprocess
    email = args.author_email or "REPLACE@users.noreply.github.com"
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.name", args.author_name],
                ["git", "config", "user.email", email],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m",
                 f"Reproduction of {args.paper_id}: {summary['verdict']}"]):
        subprocess.run(cmd, cwd=dest, check=False, capture_output=True)

    print(json.dumps(summary, indent=2))
    repo = args.repo_name or f"repro-{scaffold.slug(args.paper_id)}"
    print(f"\nStaged (evidence only, secret-scanned, NOT pushed): {dest}")
    print(f"To publish it as its own repo, review then run:")
    print(f"  gh repo create {repo} --public --source={dest} --remote=origin --push \\")
    print(f"     --description \"Reproduction of {args.paper_id} ({summary['verdict']}) — built with paper-repro-gym\"")
    return 0


def cmd_publish_hf(args: argparse.Namespace) -> int:
    """Publish a bundle to Hugging Face as an evidence-only dataset repo.
    Assembles + secret-scans (same as `gym publish`), adds a dataset card, and
    uploads. Needs `huggingface_hub` + `huggingface-cli login` (token never in
    chat). --dry-run stages the carded dir without uploading."""
    dest = Path(args.dest).resolve()
    citation = {"authors": args.authors, "title": args.paper_title, "year": args.paper_year,
                "venue": args.paper_venue, "doi": args.paper_doi, "reproducer": args.author_name}
    try:
        summary = publish.build_publish_repo(
            bundle_dir=Path(args.bundle).resolve(), dest=dest,
            paper_id=args.paper_id, paper_url=args.paper_url, gym_url=GYM_URL,
            citation=citation, include_code=not args.no_code,
            kind="replication" if args.replication else "reproduction")
    except publish.PublishBlocked as exc:
        print(f"PUBLISH REFUSED — {exc}", file=sys.stderr)
        return 2

    arxiv = hf_publish.arxiv_id_from(args.paper_id, args.paper_url)
    hf_publish.add_card_frontmatter(
        dest / "README.md",
        hf_publish.dataset_card_frontmatter(citation, summary["verdict"], arxiv))
    print(json.dumps({**summary, "hf_dataset_card": True, "arxiv": arxiv}, indent=2))

    if args.dry_run:
        print(f"\n--dry-run: carded dataset staged at {dest} (NOT uploaded).")
        return 0
    try:
        url = hf_publish.publish_dataset(dest, args.repo_id, private=args.private)
    except RuntimeError as exc:
        print(f"\nHF upload not done: {exc}", file=sys.stderr)
        return 1
    print(f"\nPublished to Hugging Face: {url}")
    return 0


def cmd_council(args: argparse.Namespace) -> int:
    """Run the adversarial review council over a finished bundle: a panel of
    independent reviewers attacks the evidence from five angles, then a judge
    argues against the panel before ruling.

    The council can DISPUTE a reproduction; it can never certify one, and it
    never alters the mechanical verdict. Needs `anthropic` + an API key —
    the only part of the gym that talks to a model.

    Exits 3 when credibility is DISPUTED, so CI can gate on it."""
    bundle_dir = Path(args.bundle).resolve()
    state = council.review_state(bundle_dir)
    if state["reviewed"] and not state["stale"] and not args.force:
        print(f"{bundle_dir / 'council.json'} already reviews this exact evidence "
              f"(credibility {state['credibility']}). Re-running costs model calls for "
              f"the same input — pass --force if that is what you want.", file=sys.stderr)
        return 0
    if args.panel_model:
        council.PANEL_MODEL = args.panel_model
    if args.judge_model:
        council.JUDGE_MODEL = args.judge_model

    record = council.run_council(bundle_dir)
    if not args.dry_run:
        council.write_council(bundle_dir, record)

    print(json.dumps({
        "paper_id": record["paper_id"],
        "mechanical_verdict": record["mechanical_verdict"],
        "credibility": record["credibility"],
        "objections": len(record["objections"]),
        "upheld": len(record["upheld"]),
        "open_checks": len(record["judge"]["open_checks"]),
        "usage": record["usage"],
    }, indent=2))
    if args.dry_run:
        print(f"\n--dry-run: nothing written to {bundle_dir}.")
    else:
        print(f"\nWrote {bundle_dir / 'council.json'} and {bundle_dir / 'COUNCIL.md'}. "
              f"The verdict ({record['mechanical_verdict']}) is unchanged.")
    return 3 if record["credibility"] == "DISPUTED" else 0


def cmd_index(args: argparse.Namespace) -> int:
    """The reproducibility bench: aggregate every reproduction under a directory."""
    idx = index_mod.build_index(Path(args.dir).resolve())
    md = index_mod.render_md(idx)
    if args.write:
        Path(args.write).write_text(md, encoding="utf-8")
        print(f"wrote {args.write} ({idx['summary']['total']} reproduction(s))")
    else:
        print(md)
    return 0


def _path_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def cmd_clean(args: argparse.Namespace) -> int:
    """Reclaim disk after publishing. Removes the run/bundle/quarantine runtime
    (safe to delete — the published repo is the source of truth) and, with
    --artifacts, the acquired inputs (re-downloadable from the official source).
    --purge removes the whole path. Always --dry-run first if unsure."""
    target = Path(args.path).resolve()
    to_remove: list[Path] = []
    if args.purge:
        to_remove = [target]
    else:
        for rel in (".gym/runs", ".gym/bundles", ".quarantine"):
            d = target / rel
            if d.exists():
                to_remove.append(d)
        for f in ("approval.json", "approval.signed.json", ".scan_block", "acquisition.json"):
            fp = target / f
            if fp.exists():
                to_remove.append(fp)
        if args.artifacts and (target / "inputs").is_dir():
            to_remove.append(target / "inputs")

    total = sum(_path_size(p) for p in to_remove)
    if not to_remove:
        print(f"nothing to clean under {target}")
        return 0
    if args.dry_run:
        print(f"would free {_human(total)} from {len(to_remove)} path(s):")
        for p in to_remove:
            print(f"  {p}  ({_human(_path_size(p))})")
        return 0

    import shutil
    for p in to_remove:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    print(f"freed {_human(total)} ({len(to_remove)} path(s) removed under {target})")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the bundled hello_repro example through the whole gated flow, to
    prove the machinery end to end. Uses an ephemeral approval secret."""
    exp = _asset("examples") / "hello_repro"
    workdir = Path(args.workdir).resolve()
    os.environ.setdefault("GYM_APPROVAL_SECRET", _secrets.token_hex(16))
    ns = argparse.Namespace(experiment=str(exp), approved_by="demo", workdir=str(workdir))
    if cmd_approve(ns) or cmd_sign(ns):
        return 1
    if cmd_run(ns):
        print("demo run did not complete", file=sys.stderr)
        return 1
    run_id = sorted((workdir / "runs").glob("*"))[-1].name
    return cmd_bundle(argparse.Namespace(experiment=str(exp), workdir=str(workdir), run_id=run_id))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gym", description="paper reproduction workbench (containment, not a sandbox)")
    p.add_argument("--workdir", default=".gym", help="where runs/ and bundles/ are written")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="policy status").set_defaults(func=cmd_check)
    sub.add_parser("preflight", help="report boundary strength; non-zero if not hardened").set_defaults(func=cmd_preflight)
    for name, fn, extra in [
        ("scaffold", cmd_scaffold, [("dossier", {}), ("dest", {}), ("--id", {"default": None})]),
        ("pin", cmd_pin, [("image", {})]),
        ("acquire", cmd_acquire, [("experiment", {}),
                                  ("--allow-findings", {"action": "store_true",
                                   "help": "override the scan-gate after reviewing findings"})]),
        ("approve", cmd_approve, [("experiment", {}), ("--approved-by", {"default": "operator"})]),
        ("sign", cmd_sign, [("experiment", {})]),
        ("run", cmd_run, [("experiment", {}),
                          ("--require-hardened", {"action": "store_true",
                           "help": "refuse to run unless the boundary is hardened (rootless podman)"})]),
        ("bundle", cmd_bundle, [("experiment", {}), ("run_id", {})]),
        ("figures", cmd_figures, [("source", {}), ("dest", {}),
                                  ("--label", {"default": ""})]),
        ("remote", cmd_remote, [("action", {"choices": ["preflight", "submit", "status", "fetch"]}),
                                ("--provider", {"default": "kaggle"}),
                                ("--path", {"default": None, "help": "payload dir to scan/submit"}),
                                ("--ref", {"default": None, "help": "provider job ref, e.g. user/kernel-slug"}),
                                ("--dest", {"default": None, "help": "where fetch writes outputs"})]),
        ("import-run", cmd_import_run, [("experiment", {}),
                                        ("--metrics", {"required": True, "help": "metrics.json produced off-box"}),
                                        ("--ran-on", {"required": True, "help": "where it ran, e.g. 'Google Colab (T4)'"}),
                                        ("--image", {"default": ""}),
                                        ("--command", {"default": ""})]),
        ("publish", cmd_publish, [("bundle", {}), ("dest", {}),
                                  ("--paper-id", {"required": True}),
                                  ("--paper-url", {"default": ""}),
                                  ("--authors", {"default": "", "help": "original authors, semicolon-separated: 'Last, First; ...'"}),
                                  ("--paper-title", {"default": ""}),
                                  ("--paper-year", {"default": ""}),
                                  ("--paper-venue", {"default": "", "help": "journal / conference"}),
                                  ("--paper-doi", {"default": ""}),
                                  ("--repo-name", {"default": None}),
                                  ("--author-name", {"default": "reproduction",
                                   "help": "you, the reproducer (credited separately from the original authors)"}),
                                  ("--author-email", {"default": None,
                                   "help": "use your GitHub <id>+<user>@users.noreply.github.com"}),
                                  ("--no-code", {"action": "store_true",
                                   "help": "omit the harness code/ (e.g. license-sensitive artifacts)"}),
                                  ("--replication", {"action": "store_true",
                                   "help": "the harness was re-implemented from the paper's text rather than running the authors' artifacts (ACM \"Results Replicated\")"})]),
        ("council", cmd_council, [("bundle", {}),
                                  ("--panel-model", {"default": None,
                                   "help": "override the panel model (default claude-sonnet-5)"}),
                                  ("--judge-model", {"default": None,
                                   "help": "override the judge model (default claude-opus-5)"}),
                                  ("--dry-run", {"action": "store_true",
                                   "help": "review and print, but write nothing into the bundle"}),
                                  ("--force", {"action": "store_true",
                                   "help": "re-review even if a current council.json exists"})]),
        ("index", cmd_index, [("dir", {}), ("--write", {"default": None,
                               "help": "write the board to a markdown file (e.g. INDEX.md)"})]),
        ("clean", cmd_clean, [("path", {}),
                              ("--artifacts", {"action": "store_true",
                               "help": "also remove acquired inputs/ (re-downloadable)"}),
                              ("--purge", {"action": "store_true", "help": "remove the whole path"}),
                              ("--dry-run", {"action": "store_true"})]),
        ("publish-hf", cmd_publish_hf, [("bundle", {}), ("dest", {}),
                                        ("--repo-id", {"required": True, "help": "owner/name for the HF dataset"}),
                                        ("--paper-id", {"required": True}),
                                        ("--paper-url", {"default": ""}),
                                        ("--authors", {"default": ""}),
                                        ("--paper-title", {"default": ""}),
                                        ("--paper-year", {"default": ""}),
                                        ("--paper-venue", {"default": ""}),
                                        ("--paper-doi", {"default": ""}),
                                        ("--author-name", {"default": "reproduction"}),
                                        ("--private", {"action": "store_true"}),
                                        ("--no-code", {"action": "store_true"}),
                                        ("--replication", {"action": "store_true",
                                         "help": "the harness was re-implemented from the paper's text rather "
                                                 "than running the authors' artifacts (ACM \"Results Replicated\")"}),
                                        ("--dry-run", {"action": "store_true",
                                         "help": "stage the carded dataset without uploading"})]),
        ("demo", cmd_demo, []),
    ]:
        sp = sub.add_parser(name)
        for arg, kw in extra:
            sp.add_argument(arg, **kw)
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except core.GateError as exc:
        print(f"GATE REFUSED: {exc}", file=sys.stderr)
        return 2
    except council.CouncilError as exc:
        # An absent council is reported as absent, never as approval.
        print(f"COUNCIL DID NOT RUN: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
