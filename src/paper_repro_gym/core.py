"""Safety-critical engine: A1 approval gate, artifact acquisition/quarantine,
static scanning, and the locked-down container run.

Stdlib + the docker CLI only.

HONEST FRAMING (load-bearing, repeated in SECURITY.md): this is CONTAINMENT,
not a security sandbox. On a host where the invoking user is in the `docker`
group the orchestrator is root-equivalent, and a kernel escape from the
container reaches the host. The container flags below are a real boundary
against the artifact's *code* (no network, no capabilities, non-root user,
read-only root filesystem, resource caps), but not against a kernel exploit.
Nothing in this package is ever described as "sandboxed". See docs/PODMAN_UPGRADE.md
for a stronger boundary.

The A1 gate is non-skippable: run_container() refuses unless a signed approval
binds (a) the exact artifact manifest hash, (b) the exact command, and (c) a
sandbox-policy hash matching the flags actually used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import grp
except ImportError:  # non-POSIX; the docker-group check simply reports unknown
    grp = None  # type: ignore

# Container runtime, selectable without editing code. `podman` (rootless) is a
# materially stronger boundary than `docker` on a shared host -- see
# docs/PODMAN_UPGRADE.md. Because the runtime is part of SANDBOX_POLICY, its
# hash changes with the runtime, so an approval signed for docker will NOT
# verify for a podman run (and vice versa) -- deliberate.
RUNTIME = os.environ.get("GYM_RUNTIME", "docker")

# The fixed container lockdown. Its hash is what an A1 approval binds, so any
# drift here invalidates every prior approval -- deliberately.
SANDBOX_POLICY: dict = {
    "runtime": RUNTIME,
    "network": "none",
    "read_only_root": True,
    "cap_drop": "ALL",
    "no_new_privileges": True,
    "user": "65534:65534",
    "pids_limit": 256,
    "memory": "8g",
    "memory_swap": "8g",
    "cpus": "4",
    "mounts": "inputs=ro, scratch=rw, output=rw; no host home, no secrets, no docker socket",
}

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
MAX_ARCHIVE_RATIO = 100  # decompressed/compressed above this = suspected bomb


class GateError(RuntimeError):
    """Raised when the A1 gate refuses. Never caught silently -- a refusal must
    stop the run, not degrade into an unapproved execution."""


# ── boundary preflight (the enforceable redline) ──────────────────────────

def _in_docker_group() -> bool:
    if grp is None:
        return False
    try:
        return grp.getgrnam("docker").gr_gid in os.getgroups()
    except (KeyError, OSError):
        return False


def preflight(runtime: str | None = None) -> dict:
    """Classify the boundary strength of the current runtime, so callers can
    REFUSE to run untrusted artifacts on a weak (root-equivalent) one.

    boundary is:
      - "hardened": rootless podman -- an escape lands as an unprivileged user;
      - "weak": docker whose user is in the `docker` group, or rootful podman --
        root-equivalent, an escape reaches the host;
      - "unknown": runtime present but strength couldn't be established.
    """
    rt = runtime or SANDBOX_POLICY["runtime"]
    info: dict = {"runtime": rt, "available": shutil.which(rt) is not None,
                  "rootless": None, "boundary": "unknown", "warnings": []}
    if not info["available"]:
        info["warnings"].append(f"{rt} not found on PATH")
        return info

    if rt == "podman":
        try:
            out = subprocess.run([rt, "info", "--format", "{{.Host.Security.Rootless}}"],
                                 capture_output=True, text=True, timeout=20)
            rootless = out.stdout.strip().lower() == "true"
            info["rootless"] = rootless
            info["boundary"] = "hardened" if rootless else "weak"
            if not rootless:
                info["warnings"].append("podman is running ROOTFUL — rootless is the hardened mode.")
        except (OSError, subprocess.SubprocessError) as exc:
            info["warnings"].append(f"could not query podman: {exc}")
    elif rt == "docker":
        info["rootless"] = False
        if _in_docker_group():
            info["boundary"] = "weak"
            info["warnings"].append(
                "docker + membership in the 'docker' group is ROOT-EQUIVALENT: a "
                "container escape reaches the host. REDLINE — do not run untrusted "
                "third-party artifacts here. Use rootless podman (see "
                "docs/PODMAN_UPGRADE.md) or a disposable VM.")
        else:
            info["warnings"].append(
                "docker without docker-group membership — likely a rootful daemon "
                "reached via sudo; still root-equivalent for the run.")
    return info


# ── hashing / canonical json ──────────────────────────────────────────────

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(obj: object) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode())


def policy_hash() -> str:
    return canonical_hash(SANDBOX_POLICY)


def manifest_of(inputs_dir: Path) -> str:
    """Deterministic hash of the input tree: each file's relative path + its
    content hash, sorted. An A1 approval binds this -- change any input byte and
    the approval no longer matches."""
    entries = [[str(f.relative_to(inputs_dir)), sha256_file(f)]
               for f in sorted(inputs_dir.rglob("*")) if f.is_file()]
    return canonical_hash(entries)


# ── A1 approval gate ──────────────────────────────────────────────────────

def make_approval(*, paper_id: str, manifest_hash: str, command: list[str],
                  allowed_domains: list[str], max_seconds: int,
                  max_output_mb: int, approved_by: str) -> dict:
    """Build (not sign) an A1 approval. A human reviews this, then signs it."""
    return {
        "gate": "A1",
        "paper_id": paper_id,
        "artifact_manifest_hash": manifest_hash,
        "command": command,
        "allowed_domains": sorted(set(allowed_domains)),
        "sandbox_policy_hash": policy_hash(),
        "limits": {"max_seconds": int(max_seconds), "max_output_mb": int(max_output_mb)},
        "approved_by": approved_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signature": None,
    }


def _signature(approval: dict, secret: str) -> str:
    body = {k: v for k, v in approval.items() if k != "signature"}
    return hashlib.sha256(
        (secret + "\x00" + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()
    ).hexdigest()


def sign_approval(approval: dict, secret: str) -> dict:
    """HMAC-style signature over the canonical approval-minus-signature. The
    secret is supplied at call time (e.g. an env var), never stored; a tampered
    field or a swapped manifest breaks it."""
    approval["signature"] = _signature(approval, secret)
    return approval


def verify_approval(approval: dict, secret: str, *, manifest_hash: str,
                    command: list[str]) -> None:
    """Raise GateError unless the approval is intact AND matches this exact run."""
    if approval.get("gate") != "A1":
        raise GateError("not an A1 approval")
    if not approval.get("signature") or approval["signature"] != _signature(approval, secret):
        raise GateError("approval signature invalid or tampered")
    if approval.get("sandbox_policy_hash") != policy_hash():
        raise GateError("sandbox policy changed since approval; re-approve required")
    if approval.get("artifact_manifest_hash") != manifest_hash:
        raise GateError("artifact does not match the approved manifest hash")
    if approval.get("command") != command:
        raise GateError("command does not match the approved command")


# ── acquisition: quarantine + checksum + scan ─────────────────────────────

def host_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in (x.lower() for x in allowed_domains))


def acquire(url: str, allowed_domains: list[str], quarantine: Path,
            expected_sha256: str | None = None, ua: str = "paper-repro-gym/1.0") -> Path:
    """Download to quarantine. Refuses disallowed domains; verifies checksum
    when one is published. Returns the quarantined path. Never extracts here."""
    if not host_allowed(url, allowed_domains):
        raise GateError(f"download host not in approved domains: {url}")
    quarantine.mkdir(parents=True, exist_ok=True)
    dest = quarantine / (sha256_bytes(url.encode())[:16] + "-" + Path(urllib.parse.urlsplit(url).path).name)

    req = urllib.request.Request(url, headers={"User-Agent": ua}, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = 0
        with dest.open("wb") as fh:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    fh.close(); dest.unlink(missing_ok=True)
                    raise GateError("download exceeded size cap")
                fh.write(chunk)

    got = sha256_file(dest)
    if expected_sha256 and got != expected_sha256.lower():
        dest.unlink(missing_ok=True)
        raise GateError(f"checksum mismatch: expected {expected_sha256}, got {got}")
    return dest


_PICKLE_EXT = {".pkl", ".pickle", ".pt", ".pth", ".bin", ".ckpt", ".joblib", ".npy", ".npz"}
_INSTALL_HOOK_RE = re.compile(r"(setup\.py|conftest\.py|\.pth$|install\.sh|Makefile|preinstall|postinstall)")


def scan_tarball(path: Path) -> list[str]:
    """Static scan of an archive WITHOUT extracting it to disk. Reports every
    finding; the caller decides go/no-go. Extraction happens only inside the
    container, never on the host. safetensors/gguf/onnx are NOT flagged."""
    findings: list[str] = []
    try:
        with tarfile.open(path) as tf:
            comp = max(path.stat().st_size, 1)
            decomp = 0
            for m in tf.getmembers():
                decomp += max(m.size, 0)
                name = m.name
                if name.startswith("/") or ".." in Path(name).parts:
                    findings.append(f"path traversal: {name}")
                if m.issym() or m.islnk():
                    findings.append(f"symlink/hardlink: {name} -> {m.linkname}")
                if m.isdev():
                    findings.append(f"device node: {name}")
                if Path(name).suffix.lower() in _PICKLE_EXT:
                    findings.append(f"pickle-class checkpoint (never load on host): {name}")
                if _INSTALL_HOOK_RE.search(name):
                    findings.append(f"install/build hook (never run on host): {name}")
            if decomp / comp > MAX_ARCHIVE_RATIO:
                findings.append(f"archive bomb suspected: {decomp}/{comp} = {decomp // comp}x")
    except tarfile.TarError as exc:
        findings.append(f"unreadable archive: {exc}")
    return findings


# ── the containerized run ─────────────────────────────────────────────────

def container_argv(image: str, inputs: Path, scratch: Path, output: Path,
                   command: list[str]) -> list[str]:
    """Build the container argv as an ARRAY -- no shell string, so nothing in a
    paper's filenames or command can be interpolated into a shell. argv[0] is
    the configured runtime (docker or podman); the flags are identical."""
    p = SANDBOX_POLICY
    return [
        p["runtime"], "run", "--rm",
        f"--network={p['network']}", "--read-only",
        f"--cap-drop={p['cap_drop']}", "--security-opt", "no-new-privileges",
        f"--user={p['user']}", f"--pids-limit={p['pids_limit']}",
        f"--memory={p['memory']}", f"--memory-swap={p['memory_swap']}", f"--cpus={p['cpus']}",
        "--workdir", "/inputs",
        "-v", f"{inputs}:/inputs:ro",
        "-v", f"{scratch}:/scratch:rw",
        "-v", f"{output}:/output:rw",
        "--stop-timeout", "10",
        image, *command,
    ]


def run_container(*, approval: dict, secret: str, image: str, inputs_dir: Path,
                  command: list[str], runs_dir: Path, require_hardened: bool = False) -> dict:
    """Execute an approved command in a locked-down container. Refuses without a
    valid A1 approval binding this exact artifact, command, and policy.

    require_hardened=True enforces the podman redline: it refuses to run at all
    unless the runtime is a hardened (rootless) boundary. Use it whenever the
    artifact is genuinely untrusted third-party code.
    """
    if require_hardened:
        pf = preflight()
        if pf["boundary"] != "hardened":
            raise GateError(
                f"require_hardened: boundary is '{pf['boundary']}', not hardened — "
                f"refusing to run untrusted code. {' '.join(pf['warnings'])}")

    inputs_dir = inputs_dir.resolve()
    manifest_hash = manifest_of(inputs_dir)
    verify_approval(approval, secret, manifest_hash=manifest_hash, command=command)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + manifest_hash[:8]
    base = runs_dir / run_id
    scratch, output = base / "scratch", base / "output"
    for d in (scratch, output):
        d.mkdir(parents=True, exist_ok=True)
        # The container runs as uid 65534 (nobody); these host dirs are owned by
        # the invoking user, so nobody cannot write unless we open the per-run
        # dirs. They are throwaway and hold only this run's output, so 0o777 is
        # scoped and safe -- without it a write silently fails while exit == 0.
        os.chmod(d, 0o777)

    timeout_s = int(approval["limits"]["max_seconds"])
    argv = container_argv(image, inputs_dir, scratch, output, command)

    started = time.time()
    killed = False
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout_s, text=True)
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        killed, rc, out, err = True, 124, "", "TIMEOUT: killed at cap"
    except FileNotFoundError:
        raise GateError("docker not available")

    cap = int(approval["limits"]["max_output_mb"]) * 1024 * 1024
    out_bytes = _dir_size(output)
    record = {
        "run_id": run_id,
        "paper_id": approval["paper_id"],
        "artifact_manifest_hash": manifest_hash,
        "sandbox_policy_hash": policy_hash(),
        "sandbox_policy": SANDBOX_POLICY,
        "container_argv": argv,
        "command": command,
        "image": image,
        "exit_code": rc,
        "killed_on_timeout": killed,
        "wall_seconds": round(time.time() - started, 2),
        "stdout_tail": (out or "")[-4000:],
        "stderr_tail": (err or "")[-4000:],
        "output_bytes": out_bytes,
        "output_over_cap": out_bytes > cap,
        "outcome": _classify(rc, killed),
        "containment_note": "containerized (no-net, cap-drop ALL, non-root, ro-root); NOT a security sandbox",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (base / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _classify(rc: int, killed: bool) -> str:
    if killed:
        return "FAILED_SAFELY"     # hit a resource/time cap
    if rc == 0:
        return "COMPLETED"         # ran; the reproduction verdict is a later step
    return "NOT_REPRODUCED"        # nonzero exit


def _dir_size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
