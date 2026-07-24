# The whole flow — how to use paper-repro-gym

This walks the full path from "a paper looks worth reproducing" to "here is a
signed evidence bundle that says whether it reproduced". Every step is either
deterministic or human-gated; nothing runs untrusted code without a signed
approval, and (with `--require-hardened`) nothing runs it on a weak boundary.

```
 dossier ──scaffold──▶ experiment skeleton
                          │  (human completes: claims+tolerances, artifacts, command)
                          ▼
                       approve ──▶ sign ──▶ run ──▶ bundle
                        (A1 gate)         (contained)   (verdict)
```

## 0. One-time setup

```bash
git clone https://github.com/kowshikgunda71/paper-repro-gym
cd paper-repro-gym
python3 tests/test_gym.py          # sanity: 12/12 groups
python3 -m paper_repro_gym.cli --workdir .gym check
```

Requirements: Python ≥ 3.11 and a container runtime. Docker works but is a
**weak** boundary on a workstation; for real third-party artifacts use rootless
podman (`docs/PODMAN_UPGRADE.md`).

Add `gym` as a convenience alias:

```bash
alias gym='PYTHONPATH=src python3 -m paper_repro_gym.cli --workdir .gym'
```

## 1. Check your boundary

```bash
gym preflight
# hardened  -> rootless podman: good for untrusted artifacts
# weak      -> docker+docker-group / rootful: OK for YOUR OWN code only
# unknown   -> runtime present, strength unclear
```

For real papers, run under podman:

```bash
export GYM_RUNTIME=podman
gym preflight          # must say "hardened"
```

## 2. Turn a candidate into an experiment

A reproduction dossier (e.g. from an upstream assessor) says a paper is *worth*
attention. Scaffold it into an experiment:

```bash
gym scaffold path/to/dossier.json ./experiments/my_paper --id ARC-2026-07-23-001
```

This creates `experiments/my_paper/` with `dossier.json`, `experiment.json`,
`claims.json`, `inputs/`, and a `TODO.md`. A `no_go` dossier is refused. The
scaffold never invents claims — you complete them next.

You can also build an experiment by hand; the layout is in the top-level README.

## 3. Register the claims (before any run)

Edit `claims.json`. For each claim the paper makes that you want to check:

```json
[
  {
    "id": "C1",
    "description": "ResNet-50 top-1 on ImageNet",
    "section": "Table 2, row 3",
    "metric": "top1_accuracy",
    "claimed_value": 76.1,
    "tolerance": 0.5,
    "tolerance_kind": "abs"
  }
]
```

`tolerance_kind` is `abs` (absolute) or `rel` (fraction of the claimed value).
**Fix tolerances now** — a tolerance chosen after seeing the result is not a
tolerance. Unset claims score `INCONCLUSIVE`, never a pass.

## 4. Acquire the artifacts

Put the paper's code/data under `inputs/`. Acquire lawfully from the official
source and respect each artifact's license — the gym never redistributes them.
For anything downloaded, use the quarantine+checksum+scan path rather than
extracting on the host. Your run must write the metrics it wants scored to
`/output/metrics.json`, e.g. `{"top1_accuracy": 75.9}`.

## 5. Describe the run

Edit `experiment.json`:

```json
{
  "image": "pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime",
  "command": ["python", "/inputs/eval.py", "--metrics", "/output/metrics.json"],
  "allowed_domains": [],
  "max_seconds": 3600,
  "max_output_mb": 500
}
```

`inputs/` is mounted read-only at `/inputs`; `/output` is writable. There is no
network inside the run.

## 6. Approve → sign → run (the A1 gate)

```bash
export GYM_APPROVAL_SECRET='<the secret held by whoever authorizes runs>'

gym approve experiments/my_paper      # writes an UNSIGNED approval; review it
gym sign    experiments/my_paper      # signs it (binds artifact hash + command + policy)
gym run     experiments/my_paper --require-hardened
```

The approval binds the exact input-tree hash, the exact command, and the
sandbox-policy hash. Change any input byte, the command, or the policy and the
signature no longer verifies — the run is refused. `--require-hardened` refuses
to start unless `preflight` is `hardened`.

Outcomes: `COMPLETED` (ran), `NOT_REPRODUCED` (nonzero exit),
`FAILED_SAFELY` (hit a time/resource cap — not a wrong result).

## 7. Bundle the evidence

```bash
gym bundle experiments/my_paper <run_id>
```

Writes `.gym/bundles/<run_id>/` with:

- `claim_result_matrix.json` — claimed vs observed vs your pre-registered
  tolerance, and the overall verdict:
  `REPRODUCED` / `PARTIAL` / `NOT_REPRODUCED` / `INCONCLUSIVE`;
- `experiment_manifest.json` — image, command, hashes, resource use;
- `provenance.json` — SLSA-subset (build level L1, self-attested);
- `REPRODUCIBILITY.md`, `README.md`, `logs/`, `LICENSE`, `CITATION.cff`.

That bundle is the artifact you keep, share, or attach to a report. A failure to
reproduce is a real, publishable result — report it as readily as a success.

## Try it end to end right now

```bash
gym demo
```

Runs the bundled first-party example (`examples/hello_repro`) through the whole
flow and prints `overall_verdict: REPRODUCED`.
