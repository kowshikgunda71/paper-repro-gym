"""Adversarial review council for a finished reproduction bundle.

WHY THIS EXISTS. The mechanical verdict answers exactly one question: did the
observed number land inside a tolerance registered before the run? That is a
real check, and it is narrow. It cannot see that the metric was read off the
wrong split, that the tolerance was set so wide nothing could fail, that the
harness hard-coded the expected value, or that the registered claims dodge the
paper's actual central result. Those are the ways a reproduction fools itself,
and none of them are arithmetic.

So: a panel of independent reviewers is paid to attack the bundle, each from a
different angle, and a separate judge that is required to argue AGAINST the
panel before ruling on it. Diverse lenses beat N copies of one reviewer, and
forcing the judge to steelman first is what stops a panel's agreement from
being mistaken for evidence.

THE LOAD-BEARING RULE — the council can dispute, never certify.

    A council finding NEVER changes overall_verdict.

This is enforced structurally, not by prompt: the judge's schema has no verdict
field to write, and this module never opens claim_result_matrix.json for
writing. An LLM panel can raise doubt about a number; it cannot manufacture
one. A council that could upgrade NOT_REPRODUCED to REPRODUCED would be the
exact self-deception this whole workbench exists to prevent.

Every objection must carry a `falsifiable_check` — a concrete thing someone
could run to settle it. An objection nobody can check is an opinion, and
opinions do not belong in an evidence bundle.

`anthropic` is an optional dependency, lazily imported (same treatment as
`huggingface_hub` in hf_publish) so the core stays standard-library only.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# Sonnet for the panel (many parallel reviewers), Opus for the judge that has to
# hold every objection in mind at once and rule against the room. Overridable
# for cost/experiment reasons; recorded in council.json either way.
PANEL_MODEL = os.environ.get("GYM_COUNCIL_PANEL_MODEL", "claude-sonnet-5")
JUDGE_MODEL = os.environ.get("GYM_COUNCIL_JUDGE_MODEL", "claude-opus-5")

# ponytail: flat char caps, not a token counter. The bundle is small by
# construction (evidence, not artifacts); swap in count_tokens if that changes.
PER_FILE_CHARS = 20_000
TOTAL_CHARS = 160_000

CREDIBILITY = ["SOUND", "QUALIFIED", "DISPUTED", "UNVERIFIABLE"]


class CouncilError(RuntimeError):
    """Raised when the council cannot run. Never degrades into a silent pass —
    an absent council is reported as absent, not as approval."""


# ── the panel: distinct attack surfaces, not five copies of one reviewer ───

PANEL: list[tuple[str, str]] = [
    ("metric-identity",
     "Does the observed value measure the same quantity the paper claimed? Hunt for "
     "a different split (train/val/test), a different subset or filtering, a "
     "different aggregation (macro vs micro, mean vs median), different units or "
     "scale (0-1 vs percent), a different number of seeds, or a metric that shares "
     "a name with the paper's but is computed differently."),
    ("tolerance-laxity",
     "Was the pre-registered tolerance wide enough to be unfalsifiable? Ask "
     "concretely: what is the range of values that would have PASSED, and would a "
     "trivial, broken, or constant-output implementation have landed inside it? A "
     "tolerance that cannot fail is not a test. Also flag tolerance_kind mismatches "
     "(rel used where abs was meant, or vice versa) that silently widen the band."),
    ("result-leakage",
     "Read the captured harness code. Could the observed value have come from "
     "anywhere other than a genuine computation — a hard-coded constant, a value "
     "parroted back from claims.json or the paper text, a cached or committed "
     "result file, a stub that returns the expected number, or a code path that "
     "silently skips the real work and still exits 0?"),
    ("containment-provenance",
     "Do the bundle's own claims about the run hold together? Check the manifest, "
     "boundary, image digest, exit code, wall time and output size against each "
     "other and against the logs. Flag: a run too fast or too cheap for the claimed "
     "computation, an unpinned image, an external/uncontained boundary presented as "
     "if contained, a missing or empty metrics file, or logs that contradict the "
     "recorded outcome."),
    ("claim-scope",
     "Do the registered claims actually test the paper's central contribution, or "
     "only a peripheral, easy, or already-implied result? Reproducing a warm-up "
     "number and reporting REPRODUCED overstates what was shown. Name what the "
     "paper's headline claim appears to be and what a reader would wrongly conclude "
     "from this bundle."),
]

_OBJECTION = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["fatal", "major", "minor"]},
        "claim_id": {"type": "string",
                     "description": "claim id from the matrix, or 'bundle' if it applies to the whole reproduction"},
        "statement": {"type": "string", "description": "the objection, one sentence"},
        "evidence": {"type": "string",
                     "description": "the specific file/field/line in the bundle that supports it"},
        "falsifiable_check": {"type": "string",
                              "description": "a concrete check someone could run to settle this objection"},
    },
    "required": ["severity", "claim_id", "statement", "evidence", "falsifiable_check"],
    "additionalProperties": False,
}

PANELIST_SCHEMA = {
    "type": "object",
    "properties": {
        "objections": {"type": "array", "items": _OBJECTION},
        "no_objection_reason": {
            "type": "string",
            "description": "if objections is empty, what you checked and why it held up; otherwise ''"},
    },
    "required": ["objections", "no_objection_reason"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "steelman": {
            "type": "string",
            "description": "the strongest case that this reproduction is sound and the panel is wrong"},
        "rulings": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "lens": {"type": "string"},
                "statement": {"type": "string"},
                "ruling": {"type": "string", "enum": ["upheld", "overruled"]},
                "reason": {"type": "string"},
            },
            "required": ["lens", "statement", "ruling", "reason"],
            "additionalProperties": False,
        }},
        "credibility": {"type": "string", "enum": CREDIBILITY},
        "rationale": {"type": "string"},
        "open_checks": {
            "type": "array", "items": {"type": "string"},
            "description": "falsifiable checks that survived and should actually be run"},
    },
    "required": ["steelman", "rulings", "credibility", "rationale", "open_checks"],
    "additionalProperties": False,
}

PANELIST_SYSTEM = """You are an adversarial reviewer on a reproduction council. \
Your job is to find the ways this reproduction could be wrong or misleading, from \
ONE assigned angle. You are not summarising and you are not being fair — other \
reviewers cover other angles and a judge will rule on what you raise.

Rules:
- Ground every objection in something actually present in the bundle. Quote the \
file and field. Never invent a detail that is not there.
- Every objection needs a falsifiable_check: a concrete thing someone could run or \
inspect that would settle it. If you cannot name one, you do not have an objection.
- Absence of evidence IS a valid objection (e.g. the harness code was not captured, \
so leakage cannot be ruled out) — say so as an objection with the check that would \
resolve it, rather than assuming the worst or the best.
- Do not object to the reproduction merely because the verdict is negative. A \
NOT_REPRODUCED result is a legitimate finding; your job is to check whether it is \
correctly established, not to argue for a different outcome.
- Return no objections if your angle genuinely holds up. A clean review is a real \
result. Do not manufacture minor objections to look thorough."""

JUDGE_SYSTEM = """You are the judge of a reproduction council. A panel of adversarial \
reviewers has attacked this reproduction bundle. You must rule on their objections.

Work in this order, and do not skip the first step:

1. STEELMAN THE REPRODUCTION. Before ruling on anything, argue the strongest case \
that this reproduction is sound and the panel is wrong. Panels agree with themselves; \
agreement is not evidence. Look for objections that are speculative, that restate the \
same worry, that would apply to any reproduction whatsoever, or that are already \
answered by something in the bundle the reviewer missed.

2. RULE on each objection: upheld or overruled, with the reason. Overrule freely — an \
objection with no supporting evidence in the bundle, or whose falsifiable_check has \
effectively already been run, should be overruled.

3. ASSIGN CREDIBILITY, which describes how much weight the EVIDENCE can bear. It is \
not a re-scoring of the result:
   SOUND        — no upheld objection materially threatens the reported result.
   QUALIFIED    — upheld objections narrow what the result shows, but it stands.
   DISPUTED     — an upheld objection, if correct, would change the reported result.
   UNVERIFIABLE — the bundle lacks the evidence needed to assess it either way.

HARD CONSTRAINT: you cannot change the reproduction's verdict, and you must not try. \
The verdict was computed mechanically against a tolerance registered before the run, \
and it stands regardless of what you conclude. You are assessing whether the evidence \
supports what the bundle says, not whether the paper is right. In particular, never \
argue that a NOT_REPRODUCED result should be treated as reproduced."""


# ── evidence packing ──────────────────────────────────────────────────────

def _clip(text: str, cap: int = PER_FILE_CHARS) -> str:
    return text if len(text) <= cap else text[:cap] + f"\n…[clipped, {len(text)} chars total]"


def gather_evidence(bundle_dir: Path) -> str:
    """Pack the bundle into one reviewable document. Evidence only — the same
    files a published reproduction contains, in a fixed order so two runs over
    the same bundle see the same input."""
    parts: list[str] = []
    total = 0
    for rel in ("claim_result_matrix.json", "experiment_manifest.json", "provenance.json",
                "claims.json", "experiment.json", "summary.json",
                "logs/stdout.txt", "logs/stderr.txt"):
        p = bundle_dir / rel
        if p.is_file():
            body = _clip(p.read_text(encoding="utf-8", errors="replace"))
            parts.append(f"===== {rel} =====\n{body}")
            total += len(body)

    code_dir = bundle_dir / "code"
    if code_dir.is_dir():
        for f in sorted(code_dir.rglob("*")):
            if not f.is_file() or total > TOTAL_CHARS:
                continue
            body = _clip(f.read_text(encoding="utf-8", errors="replace"))
            parts.append(f"===== code/{f.relative_to(code_dir)} =====\n{body}")
            total += len(body)
    else:
        parts.append("===== code/ =====\n(NOT CAPTURED — the reproduction harness "
                     "source is absent from this bundle.)")

    if not parts:
        raise CouncilError(f"no reviewable evidence found in {bundle_dir}")
    return "\n\n".join(parts)


def evidence_hash(bundle_dir: Path) -> str:
    """Hash of exactly what the reviewers were shown. A council verdict about a
    bundle is only meaningful for the bytes it actually read, so the review is
    bound to them — the same discipline the A1 gate applies to a run. Without
    this, a council.json can outlive the evidence it reviewed and keep asserting
    SOUND about a bundle that has since changed."""
    return hashlib.sha256(gather_evidence(bundle_dir).encode()).hexdigest()


def review_state(bundle_dir: Path) -> dict:
    """Is this bundle's council.json still about this bundle? Returns
    {reviewed, stale, credibility}. `stale` is None when unreviewed, and True
    when the evidence has changed since the review — which must read as
    'not reviewed', never as the old approval."""
    bundle_dir = Path(bundle_dir)
    try:
        rec = json.loads((bundle_dir / "council.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reviewed": False, "stale": None, "credibility": None}
    recorded = rec.get("evidence_sha256")
    try:
        stale = recorded != evidence_hash(bundle_dir)
    except CouncilError:
        stale = True
    return {"reviewed": True, "stale": stale, "credibility": rec.get("credibility")}


# ── model plumbing ────────────────────────────────────────────────────────

def _client():
    try:
        import anthropic
    except ImportError as exc:
        raise CouncilError(
            "the `anthropic` SDK is not installed. Install it (userspace, no sudo):\n"
            "  pip install --user anthropic\n"
            "then export ANTHROPIC_API_KEY (or run `ant auth login`). The council is "
            "the only part of the gym that talks to a model; the rest is offline.") from exc
    return anthropic.Anthropic()


def _create(client, **kw):
    """One model call. Uses the beta endpoint so a safety-classifier decline is
    re-served by the fallback model instead of coming back empty — reproduction
    dossiers legitimately touch security and life-science topics. Falls back to
    the plain endpoint on an SDK/API that does not know the parameter yet."""
    try:
        return client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"}, **kw)
    except Exception as exc:  # noqa: BLE001 -- narrowed on the next line
        if not (isinstance(exc, TypeError) or getattr(exc, "status_code", None) == 400):
            raise
        return client.messages.create(**kw)


def _ask(client, *, model: str, system: str, prompt: str, schema: dict,
         effort: str, max_tokens: int) -> tuple[dict, dict]:
    """A structured call. Returns (parsed, usage). Raises on refusal rather than
    returning a hollow result that would read as 'no objections'."""
    resp = _create(client, model=model, max_tokens=max_tokens, system=system,
                   output_config={"effort": effort,
                                  "format": {"type": "json_schema", "schema": schema}},
                   messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason == "refusal":
        cat = getattr(getattr(resp, "stop_details", None), "category", None)
        raise CouncilError(f"{model} declined to review this bundle (category={cat}). "
                           "The council did not run; no findings were produced.")
    if resp.stop_reason == "max_tokens":
        raise CouncilError(f"{model} hit max_tokens before finishing; raise max_tokens "
                           "or lower effort rather than trusting a truncated review.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    try:
        return json.loads(text), usage
    except ValueError as exc:
        raise CouncilError(f"{model} returned unparseable output: {text[:200]}") from exc


# ── the council ───────────────────────────────────────────────────────────

def _panelist(client, lens: str, brief: str, evidence: str) -> dict:
    prompt = (f"YOUR ASSIGNED ANGLE — {lens}\n{brief}\n\n"
              f"Review only from this angle. Other reviewers cover the rest.\n\n"
              f"REPRODUCTION BUNDLE\n\n{evidence}")
    try:
        parsed, usage = _ask(client, model=PANEL_MODEL, system=PANELIST_SYSTEM,
                             prompt=prompt, schema=PANELIST_SCHEMA,
                             effort="high", max_tokens=8000)
    except CouncilError as exc:
        # One dead panelist must not silently shrink the council into a pass.
        return {"lens": lens, "error": str(exc), "objections": [], "no_objection_reason": ""}
    return {"lens": lens, **parsed, "usage": usage}


def run_council(bundle_dir: Path, *, max_workers: int = 5) -> dict:
    """Run the panel and the judge over a finished bundle. Returns the council
    record; does not write it (see write_council)."""
    bundle_dir = Path(bundle_dir).resolve()
    matrix_path = bundle_dir / "claim_result_matrix.json"
    if not matrix_path.is_file():
        raise CouncilError(f"{bundle_dir} is not a reproduction bundle "
                           "(no claim_result_matrix.json). Run `gym bundle` first.")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    evidence = gather_evidence(bundle_dir)
    ev_hash = hashlib.sha256(evidence.encode()).hexdigest()
    client = _client()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        panel = list(pool.map(lambda lb: _panelist(client, lb[0], lb[1], evidence), PANEL))

    objections = [{**o, "lens": p["lens"]} for p in panel for o in p["objections"]]
    failed = [p["lens"] for p in panel if p.get("error")]

    judge_prompt = (
        f"PANEL OBJECTIONS ({len(objections)} from {len(panel) - len(failed)} reviewers"
        + (f"; reviewers that failed to report: {', '.join(failed)}" if failed else "")
        + ")\n\n"
        + (json.dumps(objections, indent=2) if objections
           else "(none — every reviewer's angle held up)")
        + f"\n\nMECHANICAL VERDICT (authoritative, not yours to change): "
          f"{matrix.get('overall_verdict')}\n\nREPRODUCTION BUNDLE\n\n{evidence}")

    ruling, judge_usage = _ask(client, model=JUDGE_MODEL, system=JUDGE_SYSTEM,
                               prompt=judge_prompt, schema=JUDGE_SCHEMA,
                               effort="high", max_tokens=16000)

    if failed and ruling["credibility"] == "SOUND":
        # A reviewer that never reported did not clear its angle. Refusing to let
        # a partial panel read as a clean sweep is the whole point of the module.
        ruling["credibility"] = "QUALIFIED"
        ruling["rationale"] = (f"[downgraded: {len(failed)} reviewer(s) failed to report — "
                               f"{', '.join(failed)}] " + ruling["rationale"])

    return {
        "schema": "paper-repro-gym/council/1",
        "paper_id": matrix.get("paper_id"),
        "mechanical_verdict": matrix.get("overall_verdict"),
        "mechanical_verdict_is_authoritative": True,
        "note": "The council assesses whether the bundle's EVIDENCE supports what it "
                "reports. It can dispute a reproduction; it can never certify one, and "
                "it never alters overall_verdict.",
        "credibility": ruling["credibility"],
        # Binds the review to the exact bytes reviewed, so a council.json cannot
        # outlive its evidence and keep asserting a stale verdict.
        "evidence_sha256": ev_hash,
        "panel_model": PANEL_MODEL,
        "judge_model": JUDGE_MODEL,
        "panel": panel,
        "objections": objections,
        "judge": ruling,
        "upheld": [r for r in ruling["rulings"] if r["ruling"] == "upheld"],
        "usage": {
            "panel_input_tokens": sum(p.get("usage", {}).get("input_tokens", 0) for p in panel),
            "panel_output_tokens": sum(p.get("usage", {}).get("output_tokens", 0) for p in panel),
            "judge_input_tokens": judge_usage["input_tokens"],
            "judge_output_tokens": judge_usage["output_tokens"],
        },
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


def write_council(bundle_dir: Path, record: dict) -> Path:
    """Write council.json + COUNCIL.md into the bundle. Never touches
    claim_result_matrix.json — the mechanical verdict is not the council's to edit."""
    bundle_dir = Path(bundle_dir)
    (bundle_dir / "council.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (bundle_dir / "COUNCIL.md").write_text(render_md(record), encoding="utf-8")
    return bundle_dir / "council.json"


def render_md(r: dict) -> str:
    upheld = r["upheld"]
    lines = [
        f"# Adversarial review — {r['paper_id']}", "",
        f"**Mechanical verdict: {r['mechanical_verdict']}** (unchanged — the council "
        "cannot alter it)",
        f"**Evidence credibility: {r['credibility']}**", "",
        f"Reviewed evidence: `sha256:{(r.get('evidence_sha256') or '?')[:16]}…` — this "
        "review is only valid for those exact bytes; change the bundle and it reads as "
        "stale, not as approval.", "",
        "A panel of independent reviewers attacked this bundle from five angles; a "
        "separate judge argued against the panel before ruling. The council assesses "
        "whether the evidence supports what the bundle reports. It can dispute a "
        "reproduction and never certifies one.", "",
        f"Panel: `{r['panel_model']}` ×{len(r['panel'])} · Judge: `{r['judge_model']}`", "",
    ]
    for p in r["panel"]:
        if p.get("error"):
            lines.append(f"- ⚠ **{p['lens']}** — reviewer failed to report: {p['error']}")
    lines += ["", "## Judge's steelman (argued before ruling)", "", r["judge"]["steelman"], ""]

    lines += [f"## Upheld objections ({len(upheld)} of {len(r['objections'])})", ""]
    if upheld:
        lines += ["| Lens | Objection | Why it stands |", "|---|---|---|"]
        lines += [f"| {u['lens']} | {u['statement']} | {u['reason']} |" for u in upheld]
    else:
        lines.append("None — every objection raised was overruled.")

    lines += ["", "## Assessment", "", r["judge"]["rationale"], ""]
    if r["judge"]["open_checks"]:
        lines += ["## Open checks", "",
                  "Concrete things that would settle what remains in dispute:", ""]
        lines += [f"- [ ] {c}" for c in r["judge"]["open_checks"]]
        lines.append("")
    lines += ["---", "",
              "_Generated by `gym council`. LLM review is a second opinion on the "
              "evidence, not a substitute for the pre-registered tolerance check — and "
              "not itself a reproducible measurement._", ""]
    return "\n".join(lines)
