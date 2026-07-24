# Reproducing frontier papers on free / educational compute

Many papers — foundation models, large agentic evals, TPU-kernel work — need
more compute than a workstation. You don't have to own a cluster. This lists the
free and academic options and shows how a `paper-repro-gym` reproduction runs on
them: **heavy run off-box, honest scoring local.**

> **Trust model changes off-box.** The gym's rootless-podman containment exists
> to run *untrusted* third-party code safely on **your own** machine. On a
> **disposable** cloud notebook or an HPC job, that ephemeral environment is
> itself the sandbox — run there, keep no secrets on it, and bring back only
> `metrics.json` and logs. `gym import-run` records the boundary honestly as
> `external:<where>`, never claiming gym containment it didn't provide.

## 1. Free notebooks — no credit card (best for a smallest-meaningful experiment)

| Service | Free hardware | Rough limits |
|---|---|---|
| [Google Colab](https://research.google.com/colaboratory/faq.html) | NVIDIA T4 GPU / TPU | ~12 h sessions, ~15–30 h/week |
| [Kaggle Notebooks](https://www.kaggle.com/docs/notebooks) (Google) | T4 ×2 / P100, TPU v3 | ~30 h/week GPU, ~20 h/week TPU |
| [HF ZeroGPU Spaces](https://huggingface.co/docs/hub/advanced-compute-options) | shared NVIDIA H200 | ~short daily quota |
| [AWS SageMaker Studio Lab](https://studiolab.sagemaker.aws/) | T4 GPU | free, no CC, session limits |
| [Lightning AI Studios](https://lightning.ai/) | GPU studio | ~monthly free hours |
| [Paperspace Gradient](https://www.paperspace.com/gradient) | free-tier GPU | good for containerized/reproducible notebooks |
| [Saturn Cloud](https://saturncloud.io/) | free GPU hours | monthly allotment |

## 2. Google specifically (you asked)

- **Colab** and **Kaggle** (above) — both Google, both free, no CC.
- **[TPU Research Cloud (TRC)](https://sites.research.google/trc/)** — free Cloud
  TPU (v4 / v5e) for ML researchers; short application, an academic/open-source
  context helps. The route for TPU-scale training/eval.
- **[Google Cloud Research Credits](https://cloud.google.com/edu/researchers)** —
  credit grants for academic researchers (GPUs/TPUs on GCP).
- **[Google for Startups Cloud – AI tier](https://cloud.google.com/startup)** —
  large credits (reportedly up to ~$350k) for eligible AI startups; H100/A3/TPU.

## 3. National / academic supercomputing (the real cluster-scale route)

- **[NAIRR Pilot](https://nairrpilot.org/)** — the US National AI Research
  Resource: free AI compute (incl. large GPU allocations) for US-affiliated
  researchers and students. The biggest free-at-scale option if you qualify.
- **[NSF ACCESS](https://access-ci.org/)** — Explore allocation is a ~1-page
  request, approved in days; Discover/Accelerate scale up. Multi-GPU clusters.
- **[DOE leadership computing](https://www.doeleadershipcomputing.org/)** —
  INCITE / ALCC time on Frontier (ORNL) and Aurora (ANL) for accepted proposals.
- **[EuroHPC JU](https://eurohpc-ju.europa.eu/)** — EU access calls (incl.
  "Development" and AI/benchmark tracks) on LUMI, Leonardo, etc.
- **[Digital Research Alliance of Canada](https://alliancecan.ca/)** — free HPC
  for Canadian academics (formerly Compute Canada).
- **Your institution's cluster** — often the fastest path if you're a student.

## 4. Credits & student programs

- **[AWS Cloud Credit for Research](https://aws.amazon.com/government-education/research-and-technical-computing/cloud-credit-for-research/)** — academic project credits.
- **[Azure for Students](https://azure.microsoft.com/free/students/)** — ~$100 free, no CC, with a .edu email.
- **[Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)** — includes some always-free Arm compute.
- **[NVIDIA academic/DGX Cloud programs](https://www.nvidia.com/en-us/industries/higher-education-research/)** — for eligible researchers.

Treat these as a **portfolio**: NSF ACCESS / NAIRR for training-scale, Colab or
Kaggle for quick experiments, TRC for TPUs, HF ZeroGPU for short inference.

## How a gym reproduction runs on these

```
 register claims (local, no compute)  →  run the experiment on the free resource
        │                                        │  writes metrics.json
        │                                        ▼
        └──────────────────────────────►  gym import-run  →  gym publish / publish-hf
                                          (scores vs your pre-registered tolerance,
                                           boundary = external:<where>)
```

1. **Register claims locally first** — write `claims.json` with the paper's
   claims and tolerances *before* running anything. This is the integrity step,
   and it costs no compute.
2. **Run on the free resource.** On Colab/Kaggle/HPC you run the paper's code
   directly (that environment is the sandbox) and write the metrics you
   registered to `metrics.json`, e.g. `{"accuracy": 0.78}`. Minimal Colab cell:
   ```python
   !git clone https://github.com/<authors>/<paper-repo> && cd <paper-repo> && pip -q install -r requirements.txt
   # ... run the smallest experiment ...
   import json; json.dump({"accuracy": acc}, open("metrics.json", "w"))
   from google.colab import files; files.download("metrics.json")   # bring it back
   ```
3. **Score + bundle locally** — no need to bring back weights or data:
   ```bash
   gym import-run experiments/my_paper --metrics ~/Downloads/metrics.json \
       --ran-on "Google Colab (T4)" --command "python eval.py"
   gym publish   .gym/bundles/<run_id> ~/repro-repos/repro-<paper> --paper-id ... --authors ...
   gym publish-hf .gym/bundles/<run_id> ~/hf-<paper> --repo-id <you>/repro-<paper> --paper-id ...
   ```
   The verdict is scored against the tolerance you fixed **before** the run, and
   the bundle records `boundary: external:Google Colab (T4)` — honest about where
   it ran.

So heavy compute is borrowed from a disposable free/academic instance, and the
gym keeps the rigor (pre-registration, claim/result matrix, provenance, honest
verdict, code + citation) for the scoring and publishing.

## Picking a feasible target

Prefer the **smallest meaningful experiment**: one seed, one dataset, one metric,
an eval of a released checkpoint rather than a from-scratch retrain. Reproducing
one headline number on free compute is a real, citable result; a full retrain is
usually not necessary to check a claim.

Sources: [free-GPU 2026 guide](https://www.thundercompute.com/blog/free-cloud-gpu-credits),
[AI compute grants (NAIRR/ACCESS/DOE)](https://grantedai.com/blog/ai-compute-grants-gpu-credits-guide),
[stackable free credits](https://www.spheron.network/blog/free-gpu-cloud-credits-2026/),
[Colab FAQ](https://research.google.com/colaboratory/faq.html),
[HF advanced compute](https://huggingface.co/docs/hub/advanced-compute-options).
