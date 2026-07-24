"""Reproducibility bundle generator.

Turns a completed container run plus its pre-registered claims into an
evidence bundle: a claim/result matrix evaluated against a tolerance that was
fixed BEFORE the run, a machine-readable experiment manifest, SLSA-style
provenance, and human-readable reproducibility + limitations docs.

The reproduction verdict is decided here, deterministically, against the
pre-registered tolerance -- never by eyeballing the output afterwards.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Code/text files (the reproducer's harness) are captured into the bundle so a
# published reproduction shows exactly what was run. Binaries, data, and model
# weights are NOT captured -- those are acquired artifacts, never redistributed.
CODE_EXTS = {".py", ".sh", ".bash", ".json", ".yaml", ".yml", ".toml", ".cfg",
             ".ini", ".txt", ".md", ".r", ".jl", ".ipynb", ".c", ".cpp", ".h",
             ".rs", ".go", ".js", ".ts", ".sql", ".mk"}
CODE_MAX_BYTES = 256 * 1024


def capture_reproduction_code(exp_dir: Path, bundle_dir: Path) -> Path | None:
    """Copy the reproduction SPEC (experiment.json, claims.json) and the
    reproducer's harness code (small code/text files under inputs/) into the
    bundle, so the bundle documents exactly what was run. Large / binary / data
    files are skipped -- those are acquired artifacts, not the reproducer's code
    and not ours to redistribute. Returns the code/ dir, or None."""
    for name in ("experiment.json", "claims.json"):
        src = exp_dir / name
        if src.exists():
            shutil.copy2(src, bundle_dir / name)
    inputs = exp_dir / "inputs"
    code_dir = bundle_dir / "code"
    if inputs.is_dir():
        for f in sorted(inputs.rglob("*")):
            if (f.is_file() and f.suffix.lower() in CODE_EXTS
                    and f.stat().st_size <= CODE_MAX_BYTES):
                dest = code_dir / f.relative_to(inputs)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
    return code_dir if code_dir.exists() else None


def evaluate_claim(claim: dict, observed: float | None) -> dict:
    """Compare one observed value against a pre-registered claim + tolerance.

    A claim: {id, description, section, metric, claimed_value, tolerance,
              tolerance_kind: "abs"|"rel"}. Returns a verdict row. INCONCLUSIVE
    when the run produced no value for the metric -- absence is not failure.
    """
    claimed = claim.get("claimed_value")
    tol = claim.get("tolerance")
    kind = claim.get("tolerance_kind", "abs")
    row = {
        "claim_id": claim.get("id"),
        "description": claim.get("description"),
        "section": claim.get("section"),
        "metric": claim.get("metric"),
        "claimed_value": claimed,
        "observed_value": observed,
        "tolerance": tol,
        "tolerance_kind": kind,
    }
    if observed is None or claimed is None or tol is None:
        row["verdict"] = "INCONCLUSIVE"
        row["reason"] = "no observed value for the metric" if observed is None else "claim/tolerance not fully specified"
        return row
    delta = abs(float(observed) - float(claimed))
    allowed = float(tol) if kind == "abs" else abs(float(claimed)) * float(tol)
    row["delta"] = round(delta, 6)
    row["allowed"] = round(allowed, 6)
    row["verdict"] = "REPRODUCED" if delta <= allowed else "NOT_REPRODUCED"
    return row


def _overall(rows: list[dict]) -> str:
    verdicts = {r["verdict"] for r in rows}
    if not rows:
        return "INCONCLUSIVE"
    if "NOT_REPRODUCED" in verdicts:
        return "NOT_REPRODUCED"
    if verdicts == {"REPRODUCED"}:
        return "REPRODUCED"
    if "REPRODUCED" in verdicts:
        return "PARTIAL"
    return "INCONCLUSIVE"


def build_bundle(*, out_dir: Path, dossier: dict, claims: list[dict],
                 observed: dict, run_record: dict, license_text: str,
                 citation_cff: str) -> dict:
    """Assemble the bundle. `observed` maps metric -> value (from the run's
    output). Returns the summary written to summary.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()

    rows = [evaluate_claim(c, observed.get(c.get("metric"))) for c in claims]
    overall = _overall(rows)

    matrix = {
        "paper_id": dossier.get("paper_id") or dossier.get("identifier"),
        "overall_verdict": overall,
        "evaluated_at": stamp,
        "note": "Verdicts use tolerances registered before the run. REPRODUCED = "
                "the authors' own artifacts reproduced the reported result within "
                "tolerance (ACM 'Results Reproduced'); it is NOT replication.",
        "claims": rows,
    }
    (out_dir / "claim_result_matrix.json").write_text(
        json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "schema": "paper-repro-gym/experiment-manifest/1",
        "paper_id": matrix["paper_id"],
        "image": run_record.get("image"),
        "image_digest": run_record.get("image_digest"),
        "digest_pinned": run_record.get("digest_pinned"),
        "boundary": run_record.get("boundary"),
        "command": run_record.get("command"),
        "artifact_manifest_hash": run_record.get("artifact_manifest_hash"),
        "sandbox_policy_hash": run_record.get("sandbox_policy_hash"),
        "sandbox_policy": run_record.get("sandbox_policy"),
        "resource_use": {
            "wall_seconds": run_record.get("wall_seconds"),
            "output_bytes": run_record.get("output_bytes"),
            "killed_on_timeout": run_record.get("killed_on_timeout"),
        },
        "run_id": run_record.get("run_id"),
        "outcome": run_record.get("outcome"),
        "created_at": stamp,
    }
    (out_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # SLSA-style provenance: what was produced, from what, how. Build level L1
    # (self-attested, unhosted) is what is honestly achievable on an operator
    # host -- we say so rather than overclaiming L2/L3.
    provenance = {
        "schema": "https://slsa.dev/provenance/v1 (subset)",
        "build_level_claimed": "L1 (self-attested, unhosted) — not L2/L3",
        "subject": [{"name": "reproduction-run", "digest": {"sha256": run_record.get("artifact_manifest_hash")}}],
        "predicate": {
            "buildType": "paper-repro-gym/container-run",
            "invocation": {"image": run_record.get("image"), "command": run_record.get("command")},
            "policy_hash": run_record.get("sandbox_policy_hash"),
            "startedOn": run_record.get("finished_at"),
        },
    }
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    # Copy raw run evidence.
    logs = out_dir / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "run.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
    (logs / "stdout.txt").write_text(run_record.get("stdout_tail", ""), encoding="utf-8")
    (logs / "stderr.txt").write_text(run_record.get("stderr_tail", ""), encoding="utf-8")

    (out_dir / "LICENSE").write_text(license_text, encoding="utf-8")
    (out_dir / "CITATION.cff").write_text(citation_cff, encoding="utf-8")

    (out_dir / "REPRODUCIBILITY.md").write_text(_repro_md(matrix, manifest, run_record), encoding="utf-8")
    (out_dir / "README.md").write_text(_readme_md(matrix), encoding="utf-8")

    summary = {"overall_verdict": overall, "bundle_dir": str(out_dir),
               "claims": len(rows), "run_id": run_record.get("run_id")}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _repro_md(matrix: dict, manifest: dict, run: dict) -> str:
    lines = [
        f"# Reproducibility — {matrix['paper_id']}", "",
        f"**Overall verdict: {matrix['overall_verdict']}**", "",
        "This is *reproduction* (re-running the authors' own artifacts), not",
        "replication. Tolerances were registered before the run.", "",
        "## Claim / result matrix", "",
        "| Claim | Metric | Claimed | Observed | Tolerance | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for r in matrix["claims"]:
        lines.append(
            f"| {r.get('description','')[:40]} | {r.get('metric')} | {r.get('claimed_value')} | "
            f"{r.get('observed_value')} | {r.get('tolerance')} ({r.get('tolerance_kind')}) | {r['verdict']} |")
    lines += [
        "", "## Environment & command", "",
        f"- Image: `{manifest['image']}`",
        f"- Command: `{' '.join(manifest['command'] or [])}`",
        f"- Artifact manifest hash: `{manifest['artifact_manifest_hash']}`",
        f"- Sandbox policy hash: `{manifest['sandbox_policy_hash']}`",
        f"- Wall seconds: {manifest['resource_use']['wall_seconds']}",
        f"- Outcome: {manifest['outcome']}",
        "", "## Containment & limitations", "",
        "- Runs are **containerized, not sandboxed**: no network, all Linux",
        "  capabilities dropped, non-root user, read-only root filesystem, and",
        "  CPU/memory/pid caps. On a host whose user is in the `docker` group the",
        "  orchestrator is root-equivalent; a kernel escape reaches the host.",
        "- Provenance is SLSA build level L1 (self-attested, unhosted).",
        "- A `FAILED_SAFELY` outcome means a resource/time cap was hit, not that",
        "  the result is wrong.",
        "",
    ]
    return "\n".join(lines)


def _readme_md(matrix: dict) -> str:
    return "\n".join([
        f"# Reproduction bundle — {matrix['paper_id']}", "",
        f"Overall verdict: **{matrix['overall_verdict']}**.", "",
        "Contents:",
        "- `claim_result_matrix.json` — claimed vs observed vs pre-registered tolerance",
        "- `experiment_manifest.json` — image, command, hashes, resource use",
        "- `provenance.json` — SLSA-subset build provenance (L1)",
        "- `REPRODUCIBILITY.md` — human-readable summary",
        "- `logs/` — raw run record, stdout, stderr",
        "- `LICENSE`, `CITATION.cff`",
        "",
        "Generated by paper-repro-gym. Reproduction, not replication.",
        "",
    ])


def copy_into_repo_bundle(bundle_dir: Path, dest_root: Path) -> Path:
    """Place a finished bundle under a repo's bundles/ dir for staging (A2).
    Local only -- no push, no external call."""
    dest = dest_root / "bundles" / bundle_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(bundle_dir, dest)
    return dest
