"""Connectors for running an experiment on external compute (Kaggle, and others
added the same way).

The gym's own sandbox is deliberately small — 4 CPUs, no GPU — so any experiment
larger than that has to leave the box. That is a integrity problem before it is
an engineering one, and this module encodes three rules:

1. **Uploading is publishing.** A payload sent to a third party is out of your
   control the moment it lands. It therefore passes the SAME secret/PII scan
   that gates `gym publish`, and a hit refuses the submission. This is the check
   that stops an API token, a private key, or a host path riding along inside a
   harness or a notebook.
2. **The gym never handles the provider's credentials.** Auth is delegated
   entirely to the provider's own CLI and its own credential store. No token is
   read, copied, logged, or written into an experiment, a bundle, or a config
   here. `gym` cannot leak what it never holds.
3. **Containment is reported, never claimed.** A remote run did not happen on a
   boundary this tool controlled. Results come back through `gym import-run`,
   which records `boundary: external:<provider>` — never a containment claim it
   cannot back.

Adding a provider: implement `submit`/`status`/`fetch`, register it in
PROVIDERS. Keep the credential rule — a connector that wants a token passed
through the gym is the wrong shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .publish import secret_findings

# Terminal states, normalised across providers so callers do not parse provider
# strings. Anything not listed is treated as still-running.
DONE = {"COMPLETE", "ERROR", "CANCELLED"}


class RemoteBlocked(RuntimeError):
    """Raised when a payload fails preflight. The submission does not happen."""


def preflight(payload_dir: Path) -> list[str]:
    """Scan a payload destined for a third party. Empty list == safe to send.

    Deliberately the same scanner as `gym publish`: sending code to someone
    else's compute has the same disclosure consequence as publishing it, and a
    weaker check here would be the obvious hole."""
    return secret_findings(Path(payload_dir))


class KaggleCompute:
    """Kaggle Notebooks (batch 'Save & Run All') as a compute target.

    Auth: entirely the `kaggle` CLI's own token store, which this class never
    reads. Session limits and quotas are Kaggle's, not ours; `submit` surfaces
    the refusal rather than retrying, because a silent retry loop against a
    weekly quota is how the budget disappears.
    """

    name = "kaggle"
    # Kaggle refuses a 3rd concurrent batch GPU session outright, so callers
    # queue rather than fire-and-forget.
    max_concurrent_gpu = 2

    def __init__(self, runner=None, cli: str = "kaggle"):
        # Injectable so the safety rules can be tested without a network or an
        # account. Production path is just subprocess.
        self._run = runner or self._subprocess
        self._cli = cli

    def _subprocess(self, args: list[str]) -> str:
        p = subprocess.run([self._cli, *args], capture_output=True, text=True)
        return (p.stdout + p.stderr).strip()

    def submit(self, payload_dir: Path, *, allow_unscanned: bool = False) -> dict:
        """Push a kernel directory. Refuses if the payload trips the secret scan."""
        payload_dir = Path(payload_dir)
        findings = [] if allow_unscanned else preflight(payload_dir)
        if findings:
            raise RemoteBlocked(
                "secret/PII scan blocked remote submission (uploading is publishing):\n  "
                + "\n  ".join(findings[:20]))
        out = self._run(["kernels", "push", "-p", str(payload_dir)])
        ok = "successfully pushed" in out
        return {"provider": self.name, "submitted": ok, "message": out.splitlines()[-1] if out else "",
                "scanned": not allow_unscanned}

    def status(self, ref: str) -> dict:
        out = self._run(["kernels", "status", ref])
        state = next((s for s in ("COMPLETE", "ERROR", "CANCEL", "RUNNING", "QUEUED") if s in out),
                     "UNKNOWN")
        state = "CANCELLED" if state == "CANCEL" else state
        return {"ref": ref, "state": state, "done": state in DONE, "raw": out}

    def fetch(self, ref: str, dest: Path) -> dict:
        Path(dest).mkdir(parents=True, exist_ok=True)
        out = self._run(["kernels", "output", ref, "-p", str(dest)])
        files = sorted(p.name for p in Path(dest).iterdir() if p.is_file())
        return {"ref": ref, "dest": str(dest), "files": files, "message": out.splitlines()[-1] if out else ""}


PROVIDERS = {p.name: p for p in (KaggleCompute,)}


def get(provider: str, **kw):
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; known: {sorted(PROVIDERS)}")
    return PROVIDERS[provider](**kw)
