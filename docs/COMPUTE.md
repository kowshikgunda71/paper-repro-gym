# Reproducing frontier papers on free / educational compute

Many papers — foundation models, large agentic evals, TPU-kernel work — need
more compute than a single workstation. You don't have to own a cluster. This
guide lists free and academic options and how a `paper-repro-gym` reproduction
maps onto them.

> **Trust model changes off-box.** The gym's rootless-podman containment exists
> to run *untrusted* third-party code safely on **your own** machine. On a
> **disposable** free-cloud VM or notebook, the ephemeral instance is itself the
> sandbox — run there, throw it away, and bring back only the metrics and logs.
> Never put long-lived secrets on a shared free notebook.

## Free notebooks (no credit card, best for a smallest-meaningful experiment)

| Service | Free hardware | Limits | Good for |
|---|---|---|---|
| [Google Colab](https://research.google.com/colaboratory/faq.html) | NVIDIA T4 GPU / TPU | ~12 h sessions, ~15–30 h/week | quick single-GPU experiments |
| [Kaggle Notebooks](https://www.kaggle.com/docs/notebooks) | T4 ×2 / P100 GPU, TPU | ~30 h/week GPU, 20 h/week TPU, 12 h/session, no CC | small training + eval |
| [HF ZeroGPU Spaces](https://huggingface.co/docs/hub/advanced-compute-options) | shared NVIDIA H200 (70/141 GB) | ~3.5 min/day free quota | short inference / demos |

## Academic programs (bigger jobs, need eligibility)

- **[Google TPU Research Cloud (TRC)](https://sites.research.google/trc/)** —
  free Cloud TPU v4 for ML researchers; applications reviewed, no institutional
  affiliation strictly required but an academic/open-source context helps. Ideal
  for TPU-kernel and training-scale reproductions.
- **[NSF ACCESS](https://access-ci.org/)** — free HPC incl. multi-GPU clusters
  for qualifying US researchers (Explore/Discover allocations start small and are
  quick to get). The route for genuine cluster-scale training.
- **AWS Cloud Credit for Research / Google Cloud Research Credits / Azure for
  Students** — research-credit programs (often up to ~$5k–$20k for academic
  projects). Apply with a short project description.
- **[HF Academia Hub](https://huggingface.co/docs/hub/academia-hub)** —
  discounted university access to HF compute for students/staff.

## How a gym reproduction runs on these

The gym splits cleanly across environments: **heavy run remote, honest scoring
local.**

1. **Register claims locally first** — write `claims.json` with the paper's
   claims and tolerances *before* you run anything. This is the integrity step
   and it costs no compute.
2. **Run the experiment on the free resource** — on Colab/Kaggle/TRC/ACCESS,
   acquire the paper's artifacts (lawfully) and run its smallest meaningful
   experiment. Have it write the metrics you registered to a `metrics.json`, e.g.
   `{"accuracy": 0.78}`.
3. **Bring back only evidence** — download `metrics.json` plus the run log. No
   need to bring back model weights or data.
4. **Score, bundle, publish locally** — drop `metrics.json` into a local run's
   `output/`, then `gym bundle` scores it against your pre-registered tolerance,
   and `gym publish` emits the evidence-only repo. `gym index` adds it to your
   bench.

So the container/hardened-boundary machinery is used when you run untrusted code
**on your own box**; when a paper needs frontier compute, you borrow a disposable
free/academic instance for the run and keep the gym's rigor (pre-registration,
claim/result matrix, provenance, honest verdict) for the scoring and publishing.

## Picking a feasible target

Prefer the **smallest meaningful experiment** a paper offers: a single seed, one
dataset, one metric, an eval of a released checkpoint rather than a from-scratch
retrain. A reproduction of one headline number on free compute is a real,
citable result; a full retrain usually is not necessary to check a claim.

Sources: [Colab FAQ](https://research.google.com/colaboratory/faq.html),
[HF advanced compute](https://huggingface.co/docs/hub/advanced-compute-options),
[HF Academia Hub](https://huggingface.co/docs/hub/academia-hub),
[free-GPU 2026 guide](https://www.thundercompute.com/blog/free-cloud-gpu-credits).
