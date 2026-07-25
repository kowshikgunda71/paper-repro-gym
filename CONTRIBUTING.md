# Contributing

Contributions welcome — especially reproductions, and especially ones that break
something.

## Setup

```bash
git clone https://github.com/kowshikgunda71/paper-repro-gym
cd paper-repro-gym
pip install -e .            # puts `gym` on your PATH
python3 tests/test_gym.py   # offline tests, + a live containment canary if docker is present
```

The install must be **editable**. The tool reads `LICENSE`, `CITATION.cff` and
`examples/` from the repo checkout to stamp into bundles; a non-editable install
can't find them and says so rather than writing an incomplete bundle.

No test framework, no fixtures — `tests/test_gym.py` is plain asserts and runs in
a couple of seconds. Add checks there, in the same style. Tests must pass without
network access and without an API key; the council's model call is stubbed.

## The four rules

Everything else is negotiable. These are not, because each one exists to stop the
tool from telling a comfortable lie.

**1. Nothing may weaken the A1 gate.** A run happens only with a signed approval
binding the artifact manifest hash, the exact command, and the sandbox policy
hash. No bypass flag, no "skip for local dev", no widening of what counts as a
match. If a change means an approval signed for one thing can authorize another,
it's wrong regardless of how convenient it is.

**2. The council can dispute, never certify.** A council finding must never
change `overall_verdict`, and this is enforced structurally rather than by
prompt — the judge's output schema has no verdict field, and `council.py` never
opens `claim_result_matrix.json` for writing. A PR that lets model output reach
a verdict, however indirectly, will be declined. An LLM can raise doubt about a
number; it must not be able to manufacture one.

**3. Say "containment", never "sandbox".** The container is a real boundary
against the artifact's *code* — no network, capabilities dropped, non-root,
read-only root, capped. It is **not** a boundary against a kernel exploit, and on
a host whose user is in the `docker` group the orchestrator is root-equivalent.
Docs, code comments, and output all say so. Overclaiming the boundary is the one
thing that would make this tool actively harmful.

**4. Failure states must stay visible.** Absence of a check is reported as
absence, never as a pass: a resource cap yields `FAILED_SAFELY`, a missing metric
yields `INCONCLUSIVE`, an unreviewed bundle reads `not reviewed`, a review whose
evidence has changed reads `stale review`, and a reviewer that fails to report
downgrades the result. If you add a code path that can fail, make sure the
failure is louder than the success.

## Good first issues

- A sixth reviewer lens for the council (statistical power? seed sensitivity?)
- A non-Python example experiment (R, Julia) under `examples/`
- SPDX SBOM of the run image, recorded in the manifest
- N-run variance for statistical claims, so a tolerance can be checked against
  spread rather than a single draw
- Per-approver keypairs to replace the shared HMAC secret

## Reporting a reproduction

Open an issue with the published bundle or repo link. Negative results are the
most useful thing you can contribute — a paper that does not reproduce within a
tolerance registered beforehand is a finding, not a bug report against the paper.

## Security

See [SECURITY.md](SECURITY.md). Please don't file boundary-escape reports as
public issues.
