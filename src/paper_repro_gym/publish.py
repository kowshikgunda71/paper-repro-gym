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
# (the paper's artifacts) — those are never redistributed. CITATION.cff is NOT
# copied from the bundle: it is generated to cite the ORIGINAL paper.
EVIDENCE_FILES = [
    "claim_result_matrix.json", "experiment_manifest.json", "provenance.json",
    "summary.json", "REPRODUCIBILITY.md", "LICENSE",
    # The reproduction SPEC — the exact command + the pre-registered claims.
    # These are the reproducer's own config and contain no paper artifacts.
    "experiment.json", "claims.json",
    # Adversarial review, when `gym council` was run. Published alongside the
    # result rather than kept private: a reproduction that survived attack and
    # one that was never attacked should not look identical to a reader.
    "council.json", "COUNCIL.md",
]


def _authors_list(authors: str) -> list[str]:
    """Split an authors string ('Anscombe, F.J.; Doe, J.') into names.
    Semicolon-separated to avoid splitting 'Last, First' on the comma."""
    parts = [a.strip() for a in (authors or "").split(";") if a.strip()]
    return parts or ([authors.strip()] if authors and authors.strip() else [])


def _citation_cff(citation: dict, verdict: str) -> str:
    """A CITATION.cff for the REPRODUCTION that cites the ORIGINAL paper as a
    reference, so anyone citing this repo is pointed at the authors' work."""
    orig_authors = _authors_list(citation.get("authors", ""))
    lines = [
        "cff-version: 1.2.0",
        'message: "This is an independent reproduction. Please cite BOTH this'
        ' reproduction and the original paper listed under references."',
        f'title: "Reproduction of: {citation.get("title") or citation.get("paper_id")}"',
        "type: dataset",
        f'abstract: "Independent reproduction (verdict: {verdict}) of the paper below,'
        ' produced with paper-repro-gym. Evidence only; the original artifacts are'
        ' not redistributed."',
        "authors:",
        f'  - name: "{citation.get("reproducer") or "reproduction author"}"',
        "license: MIT",
        "references:",
        "  - type: article",
        f'    title: "{citation.get("title") or "(title unavailable)"}"',
    ]
    if orig_authors:
        lines.append("    authors:")
        for a in orig_authors:
            lines.append(f'      - name: "{a}"')
    else:
        lines += ["    authors:", '      - name: "(original authors — see the paper)"']
    if citation.get("year"):
        lines.append(f'    year: {citation["year"]}')
    if citation.get("venue"):
        lines.append(f'    journal: "{citation["venue"]}"')
    if citation.get("doi"):
        lines.append(f'    doi: "{citation["doi"]}"')
    if citation.get("url"):
        lines.append(f'    url: "{citation["url"]}"')
    return "\n".join(lines) + "\n"

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
                       paper_url: str, gym_url: str, citation: dict | None = None,
                       include_code: bool = True) -> dict:
    """Assemble an evidence-only reproduction repo at `dest`. Scans for secrets
    and REFUSES (PublishBlocked) if any are found. Does not touch git or the
    network — that is the caller's separately-gated step.

    `citation` (authors/title/year/venue/doi/reproducer) drives a CITATION.cff
    that credits the ORIGINAL paper, and a citation block in the README."""
    if not (bundle_dir / "claim_result_matrix.json").exists():
        raise FileNotFoundError(f"not a bundle: {bundle_dir}")
    matrix = json.loads((bundle_dir / "claim_result_matrix.json").read_text(encoding="utf-8"))
    verdict = matrix.get("overall_verdict", "INCONCLUSIVE")
    citation = {**(citation or {}), "paper_id": paper_id, "url": paper_url}

    dest.mkdir(parents=True, exist_ok=True)
    for name in EVIDENCE_FILES:
        src = bundle_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
    if (bundle_dir / "logs").is_dir():
        shutil.copytree(bundle_dir / "logs", dest / "logs", dirs_exist_ok=True)
    # The reproducer's harness code (captured by `gym bundle`) — publish it so
    # the reproduction is transparent, unless --no-code was passed for a
    # license-sensitive artifact. Paper artifacts are never in here.
    has_code = include_code and (bundle_dir / "code").is_dir()
    if has_code:
        shutil.copytree(bundle_dir / "code", dest / "code", dirs_exist_ok=True)

    # Cite the ORIGINAL paper (not the gym) — academic-integrity requirement.
    (dest / "CITATION.cff").write_text(_citation_cff(citation, verdict), encoding="utf-8")
    (dest / "README.md").write_text(
        _readme(paper_id, paper_url, gym_url, verdict, matrix, citation, has_code), encoding="utf-8")
    (dest / "ACQUISITION.md").write_text(_acquisition(paper_id, paper_url), encoding="utf-8")
    (dest / ".gitignore").write_text("inputs/\n*.tar*\n*.ckpt\n*.pt\n*.pth\n*.safetensors\n__pycache__/\n", encoding="utf-8")

    # Redact host home paths from the copied evidence, THEN scan. The scan is a
    # hard backstop: if anything sensitive survives, the build is refused.
    sanitize_tree(dest)
    findings = secret_findings(dest)
    if findings:
        raise PublishBlocked("secret/PII scan blocked publish:\n  " + "\n  ".join(findings[:20]))

    return {"dest": str(dest), "verdict": verdict, "paper_id": paper_id,
            "code_published": has_code,
            "files": sorted(p.name for p in dest.iterdir() if p.is_file())}


def _format_citation(citation: dict) -> str:
    """A one-line human citation of the original paper."""
    authors = "; ".join(_authors_list(citation.get("authors", ""))) or "(authors — see paper)"
    bits = [authors]
    if citation.get("year"):
        bits.append(f"({citation['year']})")
    if citation.get("title"):
        bits.append(f"*{citation['title']}*.")
    if citation.get("venue"):
        bits.append(f"{citation['venue']}.")
    if citation.get("doi"):
        bits.append(f"https://doi.org/{citation['doi']}")
    elif citation.get("url"):
        bits.append(citation["url"])
    return " ".join(bits)


def _readme(paper_id: str, paper_url: str, gym_url: str, verdict: str,
            matrix: dict, citation: dict, has_code: bool = False) -> str:
    rows = matrix.get("claims", [])
    reproduced = sum(1 for r in rows if r.get("verdict") == "REPRODUCED")
    lines = [
        f"# Reproduction: {citation.get('title') or paper_id}", "",
        f"**Verdict: {verdict}**  ({reproduced}/{len(rows)} claims reproduced within their"
        " pre-registered tolerance)", "",
        f"An independent *reproduction* (ACM \"Results Reproduced\") — re-running the "
        f"authors' own artifacts and checking the reported numbers against tolerances "
        f"registered **before** the run.",
        "",
        "## Paper reproduced", "",
        f"> {_format_citation(citation)}", "",
        f"Original work by the authors above; all credit for the research is theirs. "
        f"This repository is an independent reproduction, not the original work, and "
        f"does not redistribute the paper's code, data, or models — see "
        f"[ACQUISITION.md](ACQUISITION.md). See [CITATION.cff](CITATION.cff) to cite "
        f"both this reproduction and the original paper.",
        "",
        f"Produced with [paper-repro-gym]({gym_url}), a gated, containerized "
        f"reproduction workbench.",
        "",
        "## Results (reported honestly)", "",
        "Every registered claim is shown with its verdict — reproduced, **not "
        "reproduced**, partial, or inconclusive alike. A failure to reproduce is a "
        "real, reportable result and is never hidden.",
        "",
        "| Claim | Metric | Claimed | Observed | Tolerance | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {str(r.get('description',''))[:40]} | {r.get('metric')} | {r.get('claimed_value')} | "
            f"{r.get('observed_value')} | {r.get('tolerance')} ({r.get('tolerance_kind')}) | {r.get('verdict')} |")
    lines += [
        "", "## How it was reproduced", "",
        "- `experiment.json` — the exact image and command that was run.",
        "- `claims.json` — the claims and tolerances, registered before the run.",
    ]
    if has_code:
        lines.append("- `code/` — the reproduction harness (the scripts that were run). "
                     "This is the reproducer's own code; the paper's artifacts are **not** "
                     "redistributed (see [ACQUISITION.md](ACQUISITION.md)).")
    else:
        lines.append("- The paper's artifacts and the run command are described in "
                     "`experiment_manifest.json`; artifacts are obtained per "
                     "[ACQUISITION.md](ACQUISITION.md), not redistributed here.")
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
