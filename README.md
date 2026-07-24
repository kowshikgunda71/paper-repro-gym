# paper-repro-gym

A gated, containerized **workbench for reproducing research-paper results** —
re-running the authors' own artifacts and checking the reported numbers against
a tolerance you register *before* the run.

> **Reproduction, not replication.** Using ACM's current terminology, re-running
> the authors' own artifacts is *reproduction* ("Results Reproduced"), not
> *replication* (which needs independently developed artifacts). This tool does
> reproduction and never claims otherwise.

> **Containment, not a sandbox.** Runs happen in a locked-down container (no
> network, all capabilities dropped, non-root, read-only root filesystem,
> resource caps). That is a real boundary against the artifact's *code* — but on
> a host whose user is in the `docker` group the orchestrator is root-equivalent
> and a kernel escape reaches the host. Nothing here is called "sandboxed". See
> [SECURITY.md](SECURITY.md) and [docs/PODMAN_UPGRADE.md](docs/PODMAN_UPGRADE.md).

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
  manifest, SLSA-L1 provenance, raw logs, LICENSE, CITATION).

Negative and inconclusive results are first-class: a failure to reproduce is a
finding, and hitting a resource cap yields `FAILED_SAFELY`, not a false pass.

## Install

Python ≥ 3.11 and Docker. No third-party Python dependencies — standard library
only.

```bash
git clone https://github.com/kowshikgunda71/paper-repro-gym
cd paper-repro-gym
python3 tests/test_gym.py          # offline tests (+ live demo if docker present)
```

## Quickstart — the built-in example

```bash
PYTHONPATH=src python3 -m paper_repro_gym.cli --workdir .gym demo
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
[docs/PODMAN_UPGRADE.md](docs/PODMAN_UPGRADE.md), [SECURITY.md](SECURITY.md).

## Roadmap

- **Runtime**: docker/podman selectable via `GYM_RUNTIME` — done. Next: a
  Firecracker/microVM backend for a kernel-level boundary.
- **Acquisition**: wire `core.acquire`/`scan_tarball` into a `gym acquire`
  command with an egress allowlist and a mandatory scan-gate before a run.
- **Evidence**: add an SPDX SBOM of the container image and input checksums to
  the bundle; support N repetitions with variance for statistical claims.
- **GPU**: opt-in `--gpus` with VRAM/accelerator caps recorded in the manifest.
- **CI**: GitHub Actions to run the test suite and `gym demo` on every push.
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
