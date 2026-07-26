# Where this tool is useful beyond papers

The product here is not the replications. It is a pipeline: **freeze a numeric
claim, a method, and a tolerance before measurement; bind them to the artifact
and the exact command; score mechanically; refuse to overstate.** Papers are the
first input type, not the only one.

Two halves below. **Part A** is where the evidence supports a real need, with
sources that were opened and checked. **Part B** is unvalidated extrapolation,
labelled as such.

---

# Part A — Evidenced

## A1. The verification layer is genuinely missing (the strongest finding)

The surrounding ecosystem is well covered *except* for verification:

- **Archival/platform layer is solved and explicitly is not verification.** Code
  Ocean's own documentation states it is not a replication or reproducibility
  platform.
  <https://docs.codeocean.com/osl-guide/reproducibility-and-preservation/reproducibility/is-code-ocean-a-replication-or-reproducibility-platform>
- **Conference enforcement rests on self-reporting, not verification** —
  ([arXiv:2605.08586](https://arxiv.org/abs/2605.08586)).
- **Post-publication verification remains largely absent**, per a January 2026
  peer-reviewed position paper ([arXiv:2601.07189](https://arxiv.org/abs/2601.07189)).
- **ACM artifact badging** distinguishes "artifacts available" from "results
  validated" — the ladder exists, but availability is what most papers get.
  <https://sigir.org/general-information/acm-sigir-artifact-badging/>

## A2. Pre-registration specifically is an empty lane — and it failed once

The ML pre-registration workshop ran three editions (ICCV 2019, NeurIPS 2020,
NeurIPS 2021) and its site has been dead since a 2022-05-07 deadline.
<https://preregister.science/>

Meanwhile the **ML Reproducibility Challenge is alive and became an official
NeurIPS 2026 track**, with methods/tools and negative results already in scope.
<https://blog.neurips.cc/2026/05/04/mlrc-2026-reproducibility-as-an-official-track-at-neurips/>

**Read those two together.** Post-hoc replication has an institutional home;
pre-registration does not. That is the gap this tool occupies — and the fact that
the venue died once is a warning that it must be **worth running for one person
alone**, with no volunteer labour assumed. It is built that way deliberately.

## A3. Frontier labs already hand-produce this artifact

Gemma 3's technical report publishes a memorization evaluation under a *pinned
protocol* — fixed prefix/suffix lengths, exact vs. within-10%-edit-distance,
reported per model size ([arXiv:2503.19786](https://arxiv.org/html/2503.19786v1)).
A repeatable numeric claim under a frozen method, published because the claim has
to be defensible. That is this pipeline's output, produced manually by people
with no mandate to produce it.

## A4. The claim→artifact binding lost its home

Papers with Code is dead — `paperswithcode.com` now redirects to Hugging Face
Trending Papers; the archive is frozen. Per-paper code links survive; the
structured binding of a *reported number* to a runnable artifact does not.

**Honest counterweight:** nobody has rebuilt it in a year, which is evidence that
demand is weak or maintenance cost is what killed it. Noted, not chased.

---

# Part B — Unvalidated suggestions

> ### ⚠️ None of the following has been tested. No user has done any of it.
> These are extrapolations from the tool's design, not observed demand.

### B1. Internal A/B and evaluation discipline

The canonical open-licensed production-ML textbook (CMU 17-645, Ch. 19)
acknowledges statistical power, **declines to teach it**, and describes running
experiments until significance appears — textbook optional stopping, taught as
normal practice. If that is what the field is taught, pre-registering an
evaluation threshold before the run addresses a common failure.

*Inferred demand, not observed. No user has asked for this.*

### B2. Accuracy documentation you can defend

EU AI Act Art. 15(3) requires declared accuracy levels and metrics for high-risk
systems. Two things keep this out of the near term: the Digital Omnibus moved
Annex III standalone obligations to **2 Dec 2027**, and Art. 43 routes most of
Annex III through **internal control**, so providers self-assess.

The consequence is worth stating precisely: the buyer would be a provider's own
compliance team wanting defensible documentation, **not** a third-party auditor.
Anyone selling AI-Act verification tooling in 2026 is selling into a market with
no forcing date.

### B3. Vendor and model-card claim checking

A procurement team evaluating a vendor's accuracy/latency claims can register
those claims and tolerances before a pilot, then score mechanically. The
machinery is claim-type-agnostic. No evidence anyone does this today.

### B4. Zero-compute consistency auditing at scale

`gym audit` checks a printed number against the architecture a paper describes,
and solves for the reading that *would* reproduce it — separating a paper
contradicting itself from one whose arithmetic quietly repairs an omission.
2-for-2 on canonical papers so far, at zero GPU cost.

Demand evidence: **thin to none.** But it is independent of any compute budget,
and a defect found by arithmetic in a heavily-cited paper is legible to people who
will never read a tolerance file.

### B5. Teaching artifact

Two canonical sources name the same gap: CMU 17-645 declines to teach power, and
The Turing Way's reproducibility guide has no worked power calculation and no
pre-registration template. A worked "the answer was: not enough runs to tell"
case, plus a signed `claims.json`, fills it.

*The gap is evidenced; that this tool is the right way to fill it is inference.*
