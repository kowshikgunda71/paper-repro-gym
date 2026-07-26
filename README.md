# paper-repro-gym

[![CI](https://github.com/kowshikgunda71/paper-repro-gym/actions/workflows/ci.yml/badge.svg)](https://github.com/kowshikgunda71/paper-repro-gym/actions/workflows/ci.yml)

**Pre-registration for computational claims.** Freeze a numeric claim, the
method, and the tolerance you will accept *before* you measure; bind them
cryptographically to the artifact and the exact command; score mechanically;
refuse to overstate. Papers are the first input type, not the only one.

The unusual part is not that it re-runs experiments — plenty of tools do. It is
that the goalposts are hashed and signed before the run exists, so moving them
afterwards breaks the approval rather than quietly changing the answer.

Four refusals are built in, and they are the point:

| the tool refuses to | because |
|---|---|
| score a claim against a tolerance chosen after seeing the result | that is deciding what "close enough" means once you know the answer |
| call a claim refuted when the sample cannot resolve it | that asserts evidence a run does not contain |
| describe a run as contained when it happened on someone else's compute | containment is reported, never claimed |
| publish a secret, or upload one to third-party compute | uploading is publishing |

> **Reproduction vs replication.** ACM distinguishes re-running the authors'
> own artifacts (*reproduction*) from re-implementing from the paper's text
> (*replication*). This tool does both and labels which — `--replication`
> selects the wording, and it is never inferred.

> **Containment, not a sandbox.** Runs happen in a locked-down container (no
> network, all capabilities dropped, non-root, read-only root filesystem,
> resource caps). That is a real boundary against the artifact's *code* — but on
> a host whose user is in the `docker` group the orchestrator is root-equivalent
> and a kernel escape reaches the host. Nothing here is called "sandboxed". See
> [SECURITY.md](SECURITY.md) and [docs/PODMAN_UPGRADE.md](docs/PODMAN_UPGRADE.md).

## Beyond papers

[`USE_CASES.md`](USE_CASES.md) separates what the evidence supports from what it
does not: Part A cites sources for where a verification layer is genuinely
missing (archival platforms explicitly are not verification; conference
enforcement is self-reported; pre-registration specifically is an empty lane that
failed once as a venue). Part B is labelled extrapolation.

## Agents

Automated agents working in this bench follow [`AGENTS.md`](AGENTS.md) — the
operating contract. Most of its rules exist because a specific failure already
happened here, and each names the failure it prevents. The five that are never
negotiable: never edit a registered claim after a result exists; never claim
containment you did not provide; never publish the paper's artifacts; never
publish a secret (uploading to third-party compute counts as publishing); never
report a number you did not observe.

## Why

Reproducing a paper by hand is fiddly and easy to fool yourself on: you tweak a
flag, see a number that looks close, and call it reproduced. This workbench makes
the process **honest and mechanical**:

- claims and tolerances are fixed **before** the run;
- the artifact and the exact command are **hash-bound** into a signed approval;
- the run is **contained** and its resources are capped and logged;
- the verdict (`REPRODUCED` / `NOT_REPRODUCED` / `PARTIAL` / `INCONCLUSIVE`) is
  computed against the pre-registered tolerance, not eyeballed;
- every run produces an **evidence bundle** (claim/result matrix, experiment
  manifest, SLSA-L1 provenance, raw logs, LICENSE, CITATION);
- an optional **adversarial council** then attacks that bundle looking for the
  ways the tolerance check can be satisfied without the result being real.

Negative and inconclusive results are first-class: a failure to reproduce is a
finding, and hitting a resource cap yields `FAILED_SAFELY`, not a false pass.

## How this differs from the reproduction benchmarks

There is a strong and growing family of benchmarks that ask *can an AI agent
reproduce a paper?* — [PaperBench](https://arxiv.org/abs/2504.01848) (20 ICML
papers, 8,316 gradable rubric items co-developed with the original authors),
[CORE-Bench](https://arxiv.org/abs/2409.11363) (270 tasks over 90 papers),
[ResearchCodeBench](https://arxiv.org/abs/2506.02314), and newer entrants like
NatureBench, ReplicatorBench and AstaBench.

**Those grade the agent. This grades the reproduction.** A benchmark needs a
fixed task set with known answers, so it can only ever cover papers someone has
already curated. This is the workbench you point at *your* paper — the one
nobody has built a rubric for — and it optimizes for a different output: not a
score, but an artifact a skeptical reader can check.

The specific thing it adds, which none of the benchmarks and none of the
preregistration platforms (OSF, AsPredicted) currently do for computational
work: **the tolerance is registered and hash-bound before the run.** Post-hoc
grading, however good the rubric, is still scored after seeing the number. Here
the signed A1 approval binds the artifact hash, the exact command, and the
sandbox policy, so moving the goalposts breaks the signature and the run is
refused. What it borrows back from PaperBench is the LLM-judge idea — as
`gym council`, with the deliberate constraint that the judge can *dispute* a
result but never certify one.

## Install

Python ≥ 3.11 and Docker. The core is **standard library only**; two commands
have optional lazily-imported dependencies (`anthropic` for `gym council`,
`huggingface_hub` for `gym publish-hf`). Everything else runs offline.

```bash
git clone https://github.com/kowshikgunda71/paper-repro-gym
cd paper-repro-gym
pip install -e .                   # puts `gym` on your PATH; core has no deps
python3 tests/test_gym.py          # offline tests (+ live demo if docker present)
```

Install **editable**: the tool reads `LICENSE`, `CITATION.cff` and `examples/`
from the checkout to stamp into bundles, and says so loudly rather than writing
an incomplete bundle if they're missing. Optional extras:
`pip install -e '.[council]'` (adversarial review) and `.[hf]` (HF publishing).

## Quickstart — the built-in example

```bash
gym --workdir .gym demo
```

`demo` runs the first-party `examples/hello_repro` "paper" (a deterministic
computation with a claimed result) through the full gated flow and writes a
bundle under `.gym/bundles/`. Expected overall verdict: **REPRODUCED**.

## An experiment

An experiment is a directory:

```
my_experiment/
  experiment.json   {image, command, allowed_domains?, max_seconds, max_output_mb}
  dossier.json      {paper_id, recommendation, ...}
  claims.json       [{id, description, section, metric, claimed_value, tolerance, tolerance_kind}]
  inputs/           the artifact to run; the run writes /output/metrics.json
```

The container runs `command` with `inputs/` mounted read-only at `/inputs` and a
writable `/output`. Your run must write the metrics it wants scored to
`/output/metrics.json` as `{ "<metric>": <number> }`.

## From a reproduction dossier

If you have a dossier from an upstream reproduction-candidate assessor, scaffold
it straight into an experiment (a `no_go` dossier is refused, and claims are
never fabricated — you register them):

```bash
gym scaffold path/to/dossier.json ./experiments/my_paper --id ARC-2026-07-23-001
```

## The gated flow

```bash
gym preflight                         # is the boundary hardened (rootless podman)?
export GYM_APPROVAL_SECRET=...        # whoever holds this authorizes runs
gym approve my_experiment             # build an UNSIGNED approval; review it
gym sign    my_experiment             # sign it (A1 gate)
gym run     my_experiment --require-hardened   # contained run; refuses on a weak boundary
gym bundle  my_experiment <run_id>    # evidence bundle + verdict
```

The A1 approval binds the exact artifact hash, the exact command, and the
sandbox-policy hash (including the runtime). Change any input byte, the command,
or the policy and the signature no longer verifies — the run is refused.

**Full walkthrough:** [docs/USAGE.md](docs/USAGE.md). **Boundary & redlines:**
[docs/PODMAN_UPGRADE.md](docs/PODMAN_UPGRADE.md), [SECURITY.md](SECURITY.md). **Frontier papers on
free/academic compute:** [docs/COMPUTE.md](docs/COMPUTE.md). **Free & government
data:** [docs/DATASETS.md](docs/DATASETS.md).

## Adversarial review (`gym council`)

The tolerance check answers exactly one question: did the observed number land
inside a band registered before the run? That is a real check, and it is narrow.
It cannot see that the metric came off the wrong split, that the tolerance was
set so wide nothing could fail, that the harness hard-coded the expected value,
or that the registered claims dodge the paper's headline result. **Those are the
ways a reproduction fools itself, and none of them are arithmetic.**

```bash
pip install -e '.[council]' && export ANTHROPIC_API_KEY=...
gym council .gym/bundles/<run_id>
```

Five independent reviewers (Sonnet) each attack the bundle from one angle —
metric identity, tolerance laxity, result leakage, containment/provenance, claim
scope — and a judge (Opus) is required to **argue against the panel** before
ruling on it. Diverse lenses beat N copies of one reviewer, and forcing a
steelman first is what stops a panel agreeing with itself from being mistaken
for evidence. Output: `council.json` + `COUNCIL.md` in the bundle, an evidence
credibility of `SOUND` / `QUALIFIED` / `DISPUTED` / `UNVERIFIABLE`, and a list of
**open checks** — concrete things someone could run to settle what's disputed.

> **The council can dispute, never certify.** A council finding never changes
> `overall_verdict`. This is structural, not a prompt instruction: the judge's
> schema has no verdict field to write, and the module never opens
> `claim_result_matrix.json` for writing. An LLM panel can raise doubt about a
> number; it cannot manufacture one. Every objection must carry a
> `falsifiable_check` — an objection nobody can run is an opinion, and opinions
> don't belong in an evidence bundle.

Failure modes are reported, never smoothed over: a reviewer that doesn't report
downgrades `SOUND` to `QUALIFIED` (a partial panel is not a clean sweep), a
missing harness `code/` directory is stated as uncheckable rather than assumed
fine, and a model refusal aborts with exit 4 instead of returning "no
objections". `gym council` exits 3 on `DISPUTED`, so CI can gate on it.

The review is **hash-bound to the evidence it read**, the same discipline the A1
gate applies to a run: `council.json` records the sha256 of the exact bytes the
panel saw. Change the bundle afterwards and the review reads as `stale review`,
never as the old approval. `gym index` shows review state per reproduction —
`not reviewed`, `stale review`, or the credibility plus upheld-objection count.
Re-running the council on unchanged evidence is refused without `--force`, since
it would just re-bill for the same input.

## Publishing a reproduction

This repo is the **tool**. Each paper you reproduce becomes **its own separate
repo** — evidence only, never part of this one:

```bash
gym publish .gym/bundles/<run_id> ~/repro-repos/repro-<paper> \
  --paper-id "arxiv:2601.12345" --paper-url "https://arxiv.org/abs/2601.12345" \
  --author-email "<id>+<user>@users.noreply.github.com"
```

`publish` copies only the evidence (claim/result matrix, manifest, provenance,
logs, reproducibility notes), writes an `ACQUISITION.md` pointing to the
official artifact source, **redacts host paths, and hard-refuses on any secret
or personal email** — then commits locally and prints the `gh` command to push.
It never redistributes the paper's code/data/models, and never pushes for you.

## Roadmap

- **Runtime**: docker/podman selectable via `GYM_RUNTIME` — ✅ done. Next: a
  Firecracker/microVM backend for a kernel-level boundary.
- **Acquisition**: `gym acquire` with an enforced scan-gate — ✅ done.
- **Reproducibility**: image digest pinning + digest/boundary in the manifest —
  ✅ done. Next: SPDX SBOM of the image and N-run variance for statistical claims.
- **Publishing**: `gym publish` emits a standalone, secret-scanned reproduction
  repo (GitHub); `gym publish-hf` publishes the same evidence as a Hugging Face
  **dataset** with a card (arXiv-tagged into the HF papers ecosystem) — ✅ done.
  `huggingface_hub` is the only optional dependency, lazily imported so the core
  stays standard-library only.
- **Review**: `gym council` — a five-lens adversarial panel (Sonnet) plus a
  contradicting judge (Opus) that attacks the evidence bundle and can dispute
  but never certify a reproduction — ✅ done. Next: replaying a council over an
  older bundle to measure reviewer drift, and cross-model panels.
- **Bench**: `gym index <dir>` aggregates every reproduction into one board
  (paper, verdict, review state, claims, boundary) — ✅ done.
  `--write INDEX.md` for a portfolio page.
- **CI**: GitHub Actions on every push (incl. the live containment canary) — ✅ done.
- **GPU**: opt-in `--gpus` with VRAM/accelerator caps recorded in the manifest.
- **Signing**: move from a shared HMAC secret to per-approver keypairs.

## What it will not do

No download outside approved domains, no host extraction of archives, no loading
of pickle-class checkpoints, no running of a repo's own setup script on the host,
no network inside the run, no publication. Acquiring restricted or unclear-license
artifacts, and any human-subject / clinical / biological / malware / surveillance
work, are out of scope.

## License

MIT — see [LICENSE](LICENSE). This license covers *this tool's* code only. It
never grants rights to any paper, dataset, model, or code you reproduce with it;
respect each artifact's own license.
