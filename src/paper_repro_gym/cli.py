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
import sys
from pathlib import Path

from . import core, bundle

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
    import shutil
    print(json.dumps({
        "policy_hash": core.policy_hash(),
        "sandbox_policy": core.SANDBOX_POLICY,
        "docker_available": shutil.which("docker") is not None,
        "note": "containment, not a sandbox; runs only via a signed A1 approval",
    }, indent=2))
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


def cmd_run(args: argparse.Namespace) -> int:
    exp = Path(args.experiment).resolve()
    spec = _load(exp, "experiment.json")
    approval = json.loads((exp / "approval.signed.json").read_text(encoding="utf-8"))
    workdir = Path(args.workdir).resolve()
    rec = core.run_container(
        approval=approval, secret=_secret(), image=spec["image"],
        inputs_dir=exp / "inputs", command=spec["command"], runs_dir=workdir / "runs")
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
    sub.add_parser("check", help="environment + policy status").set_defaults(func=cmd_check)
    for name, fn, extra in [
        ("approve", cmd_approve, [("experiment", {}), ("--approved-by", {"default": "operator"})]),
        ("sign", cmd_sign, [("experiment", {})]),
        ("run", cmd_run, [("experiment", {})]),
        ("bundle", cmd_bundle, [("experiment", {}), ("run_id", {})]),
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
