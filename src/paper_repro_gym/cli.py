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

from . import core, bundle, scaffold, publish, index as index_mod

SCAN_BLOCK = ".scan_block"
GYM_URL = "https://github.com/kowshikgunda71/paper-repro-gym"

MIT_LICENSE = (Path(__file__).resolve().parents[2] / "LICENSE")
CITATION = (Path(__file__).resolve().parents[2] / "CITATION.cff")


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
        license_text=MIT_LICENSE.read_text(encoding="utf-8"),
        citation_cff=CITATION.read_text(encoding="utf-8"))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Stage a completed bundle into its OWN standalone reproduction repo
    (evidence only). Hard-refuses on any secret/PII. Never pushes — prints the
    exact gh command for you to run after review."""
    dest = Path(args.dest).resolve()
    try:
        summary = publish.build_publish_repo(
            bundle_dir=Path(args.bundle).resolve(), dest=dest,
            paper_id=args.paper_id, paper_url=args.paper_url, gym_url=GYM_URL)
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


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the bundled hello_repro example through the whole gated flow, to
    prove the machinery end to end. Uses an ephemeral approval secret."""
    exp = Path(__file__).resolve().parents[2] / "examples" / "hello_repro"
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
        ("publish", cmd_publish, [("bundle", {}), ("dest", {}),
                                  ("--paper-id", {"required": True}),
                                  ("--paper-url", {"default": ""}),
                                  ("--repo-name", {"default": None}),
                                  ("--author-name", {"default": "reproduction"}),
                                  ("--author-email", {"default": None,
                                   "help": "use your GitHub <id>+<user>@users.noreply.github.com"})]),
        ("index", cmd_index, [("dir", {}), ("--write", {"default": None,
                               "help": "write the board to a markdown file (e.g. INDEX.md)"})]),
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


if __name__ == "__main__":
    raise SystemExit(main())
