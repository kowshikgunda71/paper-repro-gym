"""Turn a completed evidence bundle into a standalone, publishable reproduction
repository — one repo per paper, separate from the gym itself.

What it publishes: the EVIDENCE (claim/result matrix, manifest, provenance,
logs, reproducibility notes) plus instructions to acquire the artifacts from
their official source. What it never publishes: the paper's code, data, models,
or checkpoints (license-bound, kept private), and anything that looks like a
secret or personal email (a hard scan refuses the build if found).

A per-paper reproduction repo is a citable, self-contained record that the
result reproduced (or didn't). It links back to the gym as the tool used.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Evidence files copied verbatim from a bundle. Deliberately excludes inputs/
# (the paper's artifacts) — those are never redistributed.
EVIDENCE_FILES = [
    "claim_result_matrix.json", "experiment_manifest.json", "provenance.json",
    "summary.json", "REPRODUCIBILITY.md", "CITATION.cff", "LICENSE",
]

# Hard secret / PII patterns. A single match refuses the build. Host paths are
# included because an absolute /home/<user> path leaks a username and the local
# filesystem layout into a public repo.
_SECRET_RE = re.compile(
    r"(gh[po]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|/home/[^/\s\"']+|/Users/[^/\s\"']+"
    r"|[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# Home-directory prefixes to redact from evidence before publishing.
_HOME_RE = re.compile(r"(/home/[^/\s\"']+|/Users/[^/\s\"']+|/root(?=/|\"|\s|$))")


def sanitize_text(text: str) -> str:
    """Redact host home paths (e.g. /home/alice/...) to <HOME>, keeping the
    relative structure as evidence without leaking a username or layout."""
    return _HOME_RE.sub("<HOME>", text)


def sanitize_tree(root: Path) -> None:
    """Redact home paths in every text file under root, in place."""
    for f in sorted(root.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = sanitize_text(text)
        if cleaned != text:
            f.write_text(cleaned, encoding="utf-8")


def secret_findings(root: Path) -> list[str]:
    """Scan a directory tree; return human-readable findings (empty == clean).
    Real personal emails and host paths are flagged; the GitHub no-reply email
    form is allowed."""
    findings: list[str] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _SECRET_RE.finditer(text):
            findings.append(f"{f.relative_to(root)}: {m.group(0)[:32]}…")
    return findings


class PublishBlocked(RuntimeError):
    """Raised when the secret/PII scan finds something. Publishing is refused."""


def build_publish_repo(*, bundle_dir: Path, dest: Path, paper_id: str,
                       paper_url: str, gym_url: str) -> dict:
    """Assemble an evidence-only reproduction repo at `dest`. Scans for secrets
    and REFUSES (PublishBlocked) if any are found. Does not touch git or the
    network — that is the caller's separately-gated step."""
    if not (bundle_dir / "claim_result_matrix.json").exists():
        raise FileNotFoundError(f"not a bundle: {bundle_dir}")
    matrix = json.loads((bundle_dir / "claim_result_matrix.json").read_text(encoding="utf-8"))
    verdict = matrix.get("overall_verdict", "INCONCLUSIVE")

    dest.mkdir(parents=True, exist_ok=True)
    for name in EVIDENCE_FILES:
        src = bundle_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    if (bundle_dir / "logs").is_dir():
        shutil.copytree(bundle_dir / "logs", dest / "logs", dirs_exist_ok=True)

    (dest / "README.md").write_text(_readme(paper_id, paper_url, gym_url, verdict, matrix), encoding="utf-8")
    (dest / "ACQUISITION.md").write_text(_acquisition(paper_id, paper_url), encoding="utf-8")
    (dest / ".gitignore").write_text("inputs/\n*.tar*\n*.ckpt\n*.pt\n*.pth\n*.safetensors\n__pycache__/\n", encoding="utf-8")

    # Redact host home paths from the copied evidence, THEN scan. The scan is a
    # hard backstop: if anything sensitive survives, the build is refused.
    sanitize_tree(dest)
    findings = secret_findings(dest)
    if findings:
        raise PublishBlocked("secret/PII scan blocked publish:\n  " + "\n  ".join(findings[:20]))

    return {"dest": str(dest), "verdict": verdict, "paper_id": paper_id,
            "files": sorted(p.name for p in dest.iterdir() if p.is_file())}


def _readme(paper_id: str, paper_url: str, gym_url: str, verdict: str, matrix: dict) -> str:
    rows = matrix.get("claims", [])
    lines = [
        f"# Reproduction: {paper_id}", "",
        f"**Verdict: {verdict}**", "",
        f"An independent *reproduction* (ACM \"Results Reproduced\") of "
        f"[{paper_id}]({paper_url}) — re-running the authors' own artifacts and "
        f"checking the reported numbers against tolerances registered before the run.",
        "",
        f"Produced with [paper-repro-gym]({gym_url}), a gated, containerized "
        f"reproduction workbench. This repo holds the **evidence only** — the",
        "paper's code, data, and models are not redistributed here; see",
        "[ACQUISITION.md](ACQUISITION.md) to obtain them from the official source.",
        "",
        "## Claim / result matrix", "",
        "| Claim | Metric | Claimed | Observed | Tolerance | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {str(r.get('description',''))[:40]} | {r.get('metric')} | {r.get('claimed_value')} | "
            f"{r.get('observed_value')} | {r.get('tolerance')} ({r.get('tolerance_kind')}) | {r.get('verdict')} |")
    lines += [
        "", "## Evidence in this repo", "",
        "- `claim_result_matrix.json` — claimed vs observed vs pre-registered tolerance",
        "- `experiment_manifest.json` — image (by digest), command, hashes, resource use, boundary",
        "- `provenance.json` — SLSA-subset build provenance",
        "- `REPRODUCIBILITY.md` — how to reproduce this reproduction",
        "- `logs/` — raw run record, stdout, stderr",
        "",
        "## Reproduce it yourself", "",
        f"Clone [paper-repro-gym]({gym_url}), acquire the artifacts per ACQUISITION.md,",
        "and run the command in `experiment_manifest.json` on a hardened boundary.",
        "",
        "A failure to reproduce is a real, reportable result — this record states the",
        "verdict honestly, whatever it was.",
        "",
    ]
    return "\n".join(lines)


def _acquisition(paper_id: str, paper_url: str) -> str:
    return "\n".join([
        f"# Acquiring the artifacts — {paper_id}", "",
        "This repository does **not** redistribute the paper's code, data, models, or",
        "checkpoints. Obtain them from their official source, under their own license:",
        "",
        f"- Paper: {paper_url}",
        "- Code / data / model links: see the paper and its official repository.",
        "",
        "Place the acquired artifacts under `inputs/` in your local experiment (they are",
        "git-ignored here and must never be committed). Verify each artifact's license",
        "permits your use, and scan anything downloaded before running.",
        "",
        f"Generated {datetime.now(timezone.utc).date().isoformat()}.",
        "",
    ])
