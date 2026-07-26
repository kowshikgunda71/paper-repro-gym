"""Figures, tables and diagrams from a replication's evidence.

Deliberately runs on CPU, from the metrics a run already produced. Plotting on a
rented GPU would burn accelerator quota to draw a line chart -- the quota buys
training, not rendering.

Everything here is generated from measured values only. There is no smoothing,
no interpolation between rungs, no trend fit, and no axis that starts anywhere
other than the data. A figure in a replication repo is evidence, and a figure
that flatters the result is the same offence as a number that flatters it.

Outputs, under <dest>/:
  ladder.png          accuracy vs surviving weights, per seed + mean
  ladder_delta.png    change vs the unpruned baseline, with the zero line
  claims.png          observed vs claimed, with the pre-registered tolerance band
  reinit.png          winning ticket vs the same mask randomly reinitialised
  FIGURES.md          the tables and a mermaid diagram of how the evidence was made
"""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")                  # headless: no display on a runner
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:         # optional extra: `pip install paper-repro-gym[figures]`
    raise ModuleNotFoundError(
        "figures require matplotlib, an optional extra of paper-repro-gym. "
        "Install it with `pip install matplotlib`. Every other command works "
        "without it -- scoring a claim must never depend on being able to draw it."
    ) from exc

#: Colour-blind-safe (Okabe-Ito). Figures are read by people, including the ~8%
#: of men with a red/green deficiency; a red-vs-green pass/fail chart is unreadable
#: to them and the information is not recoverable from the caption.
C = {"ticket": "#0072B2", "random": "#D55E00", "mean": "#000000",
     "ok": "#009E73", "bad": "#CC79A7", "grid": "#DDDDDD"}


def _style(ax, xlabel, ylabel, title):
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); ax.set_title(title, fontsize=11)
    ax.grid(True, color=C["grid"], linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _levels(runs):
    """{seed: [levels]} -> ordered list of (pm, {seed: acc})."""
    by_round: dict = {}
    for seed, d in runs.items():
        for lv in d["_levels"]:
            by_round.setdefault(lv["round"], {"pm": lv["pm"], "acc": {}})["acc"][seed] = \
                100 * lv["ticket"]["test_acc"]
    return [by_round[k] for k in sorted(by_round)]


def ladder(runs: dict, dest: Path, label: str = "") -> Path:
    rows = _levels(runs)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for seed in sorted(runs):
        xs = [r["pm"] for r in rows if seed in r["acc"]]
        ys = [r["acc"][seed] for r in rows if seed in r["acc"]]
        ax.plot(xs, ys, marker="o", ms=3, lw=1, alpha=0.45,
                color=C["ticket"], label="individual seeds" if seed == min(runs) else None)
    xs = [r["pm"] for r in rows]
    ys = [st.mean(r["acc"].values()) for r in rows]
    ax.plot(xs, ys, marker="o", ms=4.5, lw=2, color=C["mean"], label=f"mean of {len(runs)} seeds")
    ax.set_xscale("log")
    ax.invert_xaxis()                      # pruning progresses left-to-right
    _style(ax, "weights remaining $P_m$ (%, log scale, pruning →)",
           "test accuracy at early stop (%)",
           f"Sparsity ladder{' — ' + label if label else ''}")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    p = dest / "ladder.png"; fig.savefig(p, dpi=160); plt.close(fig)
    return p


def ladder_delta(runs: dict, dest: Path, label: str = "") -> Path:
    rows = _levels(runs)
    base = {s: rows[0]["acc"][s] for s in rows[0]["acc"]}
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs, ys, lo, hi = [], [], [], []
    for r in rows:
        d = [r["acc"][s] - base[s] for s in r["acc"] if s in base]
        xs.append(r["pm"]); ys.append(st.mean(d)); lo.append(min(d)); hi.append(max(d))
    ax.fill_between(xs, lo, hi, alpha=0.18, color=C["ticket"], lw=0, label="min–max across seeds")
    ax.plot(xs, ys, marker="o", ms=4, lw=2, color=C["mean"], label="mean")
    ax.axhline(0, color=C["bad"], lw=1.2, ls="--", label="unpruned baseline")
    ax.set_xscale("log"); ax.invert_xaxis()
    _style(ax, "weights remaining $P_m$ (%, log scale, pruning →)",
           "accuracy change vs unpruned (pp)",
           f"Does pruning help or hurt?{' — ' + label if label else ''}")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    p = dest / "ladder_delta.png"; fig.savefig(p, dpi=160); plt.close(fig)
    return p


def claims(matrix: dict, dest: Path) -> Path | None:
    """Observed vs claimed, each with its pre-registered tolerance band.

    Values are normalised to 'distance from the claim, in units of the claim's
    own tolerance', because the claims are in different units and a shared axis
    would otherwise be meaningless. 0 = exactly the paper's value; |x| <= 1 = inside
    the registered tolerance."""
    rows = [r for r in matrix.get("claims", [])
            if r.get("observed_value") is not None and r.get("tolerance")]
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(7, 0.55 * len(rows) + 1.8))
    ys = range(len(rows))
    ax.axvspan(-1, 1, color=C["ok"], alpha=0.13, lw=0)
    ax.axvline(0, color="#666666", lw=1)
    for y, r in zip(ys, rows):
        norm = (float(r["observed_value"]) - float(r["claimed_value"])) / float(r["tolerance"])
        inside = abs(norm) <= 1
        ax.plot([0, norm], [y, y], color="#999999", lw=1, zorder=1)
        ax.scatter([norm], [y], s=70, zorder=2,
                   color=C["ok"] if inside else C["bad"],
                   marker="o" if inside else "X")
    ax.set_yticks(list(ys))
    ax.set_yticklabels([f"{r['claim_id']}  {str(r.get('metric',''))[:26]}" for r in rows], fontsize=8)
    ax.invert_yaxis()
    lim = max(2.2, max(abs((float(r["observed_value"]) - float(r["claimed_value"]))
                           / float(r["tolerance"])) for r in rows) * 1.15)
    ax.set_xlim(-lim, lim)
    _style(ax, "distance from the paper's value, in units of the pre-registered tolerance",
           "", "Pre-registered claims (shaded band = inside tolerance)")
    fig.tight_layout()
    p = dest / "claims.png"; fig.savefig(p, dpi=160); plt.close(fig)
    return p


def reinit(runs: dict, dest: Path) -> Path | None:
    """The paper's central control: same mask, original init vs a fresh one."""
    pts = [(lv["pm"], 100 * lv["ticket"]["test_acc"], 100 * lv["random"]["test_acc"])
           for d in runs.values() for lv in d["_levels"] if "random" in lv]
    if not pts:
        return None
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for pm, t, r in pts:
        ax.plot([pm, pm], [r, t], color="#BBBBBB", lw=1, zorder=1)
    ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=52, color=C["ticket"],
               label="winning ticket (original init)", zorder=2)
    ax.scatter([p[0] for p in pts], [p[2] for p in pts], s=52, color=C["random"],
               marker="s", label="same mask, randomly reinitialised", zorder=2)
    ax.set_xscale("log"); ax.invert_xaxis()
    _style(ax, "weights remaining $P_m$ (%, log scale)", "test accuracy at early stop (%)",
           "The initialisation is what matters (each line = one run)")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    p = dest / "reinit.png"; fig.savefig(p, dpi=160); plt.close(fig)
    return p


PIPELINE_DIAGRAM = """```mermaid
flowchart LR
    P["Paper<br/>(text only)"] -->|verbatim quotes| R["claims.json<br/>values + tolerances"]
    R -->|published BEFORE any run| T["public timestamp"]
    H["harness<br/>written from the text"] --> X["run<br/>contained or external:&lt;where&gt;"]
    X --> M["metrics.json"]
    M --> S["mechanical scoring"]
    R --> S
    S --> V["verdict per claim<br/>REPRODUCED / NOT / INCONCLUSIVE"]
    V --> B["evidence bundle<br/>+ figures + logs"]
    B --> G["GitHub + Hugging Face"]
    style R fill:#0072B2,color:#fff
    style V fill:#009E73,color:#fff
    style T fill:#E69F00,color:#000
```"""


def _table(runs: dict) -> list[str]:
    rows = _levels(runs)
    base = {s: rows[0]["acc"][s] for s in rows[0]["acc"]}
    seeds = sorted(runs)
    out = ["| $P_m$ (%) | " + " | ".join(f"seed {s}" for s in seeds) + " | mean | Δ vs unpruned |",
           "|" + "---|" * (len(seeds) + 3)]
    for r in rows:
        vals = [f"{r['acc'][s]:.2f}" if s in r["acc"] else "—" for s in seeds]
        m = st.mean(r["acc"].values())
        d = st.mean([r["acc"][s] - base[s] for s in r["acc"] if s in base])
        out.append(f"| {r['pm']:.2f} | " + " | ".join(vals) + f" | {m:.2f} | {d:+.2f} |")
    return out


def build(runs: dict, dest: Path, *, matrix: dict | None = None, label: str = "") -> dict:
    """Generate every figure and FIGURES.md. `runs` is {seed: metrics dict}."""
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    made = {"ladder": ladder(runs, dest, label), "ladder_delta": ladder_delta(runs, dest, label)}
    if (r := reinit(runs, dest)):
        made["reinit"] = r
    if matrix and (c := claims(matrix, dest)):
        made["claims"] = c

    any_run = next(iter(runs.values()))
    md = [f"# Figures — {label or 'replication'}", "",
          "Generated from measured values only: no smoothing, no interpolation between",
          "rungs, no trend fitting. Rendered on CPU from `metrics.json` — accelerator",
          "quota buys training, not plotting.", "",
          f"- **{len(runs)} seeds**, {len(any_run['_levels'])} pruning levels each",
          f"- device: `{any_run['_config'].get('device')}`, "
          f"{sum(d['_config'].get('wall_seconds', 0) for d in runs.values())/3600:.2f} GPU-hours total",
          ""]
    for k in ("ladder", "ladder_delta", "reinit", "claims"):
        if k in made:
            md += [f"![{k}]({made[k].name})", ""]
    md += ["## Sparsity ladder (test accuracy at early stop, %)", ""] + _table(runs)
    md += ["", "## How this evidence was produced", "", PIPELINE_DIAGRAM, ""]
    (dest / "FIGURES.md").write_text("\n".join(md), encoding="utf-8")
    made["markdown"] = dest / "FIGURES.md"
    return {k: str(v) for k, v in made.items()}
