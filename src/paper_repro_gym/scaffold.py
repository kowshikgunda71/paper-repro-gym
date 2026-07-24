"""Turn a reproduction dossier into a gym experiment skeleton.

A dossier (e.g. from an upstream reproduction-candidate assessor) says a paper
is *worth* a human's attention. It does NOT contain the paper's registered
claims, the artifact URLs, or the exact command -- those are the human-gated
steps. So this scaffolds everything derivable from the dossier and leaves the
human-required fields as explicit, un-runnable TODOs. It never fabricates a
claim or a tolerance, and never auto-acquires anything.

A `no_go` dossier is refused: you cannot scaffold an experiment for a paper the
assessor rejected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _dossier_field(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def select_dossier(payload: dict | list, dossier_id: str | None) -> dict:
    """Accept a single dossier, or a daily packet with a `dossiers` array plus
    an id to pick. Ambiguity is an error, not a silent first-match."""
    if isinstance(payload, dict) and "dossiers" in payload:
        items = payload["dossiers"]
        if dossier_id:
            for it in items:
                if it.get("dossier_id") == dossier_id or it.get("identifier") == dossier_id:
                    return it
            raise ValueError(f"dossier id {dossier_id!r} not found in packet")
        if len(items) == 1:
            return items[0]
        ids = ", ".join(str(i.get("dossier_id") or i.get("identifier")) for i in items[:8])
        raise ValueError(f"packet has {len(items)} dossiers; pass --id (one of: {ids}…)")
    if isinstance(payload, dict):
        return payload
    raise ValueError("unrecognised dossier JSON")


def scaffold_from_dossier(dossier: dict, dest: Path) -> dict:
    """Write an experiment skeleton to `dest`. Returns a summary."""
    rec = _dossier_field(dossier, "recommendation", default="unknown")
    if rec == "no_go":
        raise ValueError("dossier recommendation is no_go — refusing to scaffold an experiment")

    paper = dossier.get("paper") if isinstance(dossier.get("paper"), dict) else {}
    paper_id = _dossier_field(dossier, "identifier", "paper_id") or _dossier_field(paper, "canonical_id", "doi", "arxiv_id", default="unknown")
    title = _dossier_field(paper, "title") or _dossier_field(dossier, "title", default="")
    artifacts = dossier.get("required_artifacts") if isinstance(dossier.get("required_artifacts"), dict) else {}
    compute = dossier.get("compute_envelope") if isinstance(dossier.get("compute_envelope"), dict) else {}

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "inputs").mkdir(exist_ok=True)

    # dossier.json — the provenance of this experiment.
    (dest / "dossier.json").write_text(json.dumps({
        "paper_id": paper_id,
        "title": title,
        "recommendation": rec,
        "compute_hint": compute.get("note"),
        "artifact_claims": {k: artifacts.get(k) for k in ("code", "data", "models") if k in artifacts},
        "artifacts_verified": artifacts.get("verified", False),
        "scaffolded_at": datetime.now(timezone.utc).isoformat(),
        "source": "scaffold_from_dossier",
    }, indent=2) + "\n", encoding="utf-8")

    # experiment.json — image/command are TEMPLATES the human must complete.
    (dest / "experiment.json").write_text(json.dumps({
        "image": "TODO: base image, e.g. python:3.12-slim or a CUDA image",
        "command": ["TODO", "the", "exact", "command", "that", "runs", "the", "artifact"],
        "allowed_domains": [],
        "max_seconds": 1800,
        "max_output_mb": 200,
        "_note": "Fill image + command. allowed_domains stays [] unless acquisition needs it.",
    }, indent=2) + "\n", encoding="utf-8")

    # claims.json — NEVER fabricated. One placeholder the human must replace with
    # claims read from the paper, each with a tolerance fixed BEFORE any run.
    (dest / "claims.json").write_text(json.dumps([{
        "id": "C1",
        "description": "TODO: a falsifiable claim, verbatim from the paper",
        "section": "TODO: the exact table/figure/section it comes from",
        "metric": "TODO: the metric name your run will emit to /output/metrics.json",
        "claimed_value": None,
        "tolerance": None,
        "tolerance_kind": "abs",
        "_note": "Tolerances MUST be registered before the run. Unset claims score INCONCLUSIVE.",
    }], indent=2) + "\n", encoding="utf-8")

    (dest / "inputs" / "PLACE_ARTIFACTS_HERE.md").write_text(
        "# Inputs\n\nPlace the paper's artifacts here (the code/data the command runs).\n"
        "Acquire them lawfully from the official source — never commit a checkpoint\n"
        "or dataset you don't have redistribution rights to. Prefer `gym`'s acquire\n"
        "path (quarantine + checksum + scan) for anything downloaded.\n",
        encoding="utf-8")

    (dest / "TODO.md").write_text(_todo_md(paper_id, title, rec, artifacts, compute), encoding="utf-8")

    return {"paper_id": paper_id, "recommendation": rec, "dest": str(dest),
            "next": "complete claims.json + experiment.json + inputs/, then approve/sign/run"}


def _todo_md(paper_id, title, rec, artifacts, compute) -> str:
    return "\n".join([
        f"# Experiment scaffold — {paper_id}", "",
        f"**{title}**", "",
        f"- Recommendation from dossier: `{rec}` (a candidate, NOT clearance to run)",
        f"- Compute hint: {compute.get('note', 'n/a')}",
        f"- Artifact claims (UNVERIFIED — author claims from the abstract): "
        f"code={artifacts.get('code','?')}, data={artifacts.get('data','?')}, models={artifacts.get('models','?')}",
        "",
        "## You must complete, in order",
        "",
        "1. **Register claims** — edit `claims.json`: one to three falsifiable claims",
        "   from the paper, each with `metric`, `claimed_value`, and a `tolerance`",
        "   fixed **before** any run. Unset claims score INCONCLUSIVE, never a pass.",
        "2. **Acquire artifacts** — put the code/data under `inputs/`. Verify the",
        "   license permits your use; scan anything downloaded.",
        "3. **Set the run** — edit `experiment.json`: the base `image` and the exact",
        "   `command`. The command must write the metrics to `/output/metrics.json`.",
        "4. **Approve → sign → run** — `gym approve . && gym sign . && gym run .`.",
        "   For untrusted third-party code, add `--require-hardened` so the run",
        "   refuses on a root-equivalent docker boundary (see docs/PODMAN_UPGRADE.md).",
        "5. **Bundle** — `gym bundle . <run_id>` for the claim/result matrix + verdict.",
        "",
    ])
