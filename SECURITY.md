# Security model

## Containment, not a sandbox

Reproduction runs execute **third-party code**. This tool contains that code in a
Docker container with:

- `--network=none` — no network inside the run;
- `--cap-drop=ALL` + `--security-opt no-new-privileges` — no Linux capabilities;
- `--user=65534:65534` — runs as `nobody`, never root inside the container;
- `--read-only` root filesystem; only per-run `scratch`/`output` are writable;
- `--memory`, `--memory-swap`, `--cpus`, `--pids-limit` — resource caps;
- inputs mounted **read-only**; **no** host home dir, **no** secrets, **no**
  Docker socket mounted;
- a wall-clock timeout with kill.

This is a genuine boundary against the artifact's **code**. It is **not** a
security sandbox against a **kernel exploit**: if the host user is in the
`docker` group, invoking `docker` is root-equivalent, so a container escape via a
kernel or runtime vulnerability reaches the host. We therefore never describe a
run as "sandboxed" — only "contained".

**Do not run untrusted artifacts on a host that holds secrets or credentials you
cannot afford to lose.** Use a disposable VM or a dedicated hardened host. See
[docs/PODMAN_UPGRADE.md](docs/PODMAN_UPGRADE.md) for a stronger, rootless boundary.

## The A1 approval gate

No run happens without a signed A1 approval that binds:

- the **artifact manifest hash** (sha256 of the whole input tree),
- the **exact command**, and
- the **sandbox-policy hash**.

The signature is an HMAC over the canonical approval using a secret supplied at
call time (`$GYM_APPROVAL_SECRET`), never stored. Tampering with any bound field,
swapping the artifact, changing the command, or loosening the policy invalidates
the approval and the run is refused.

## Acquisition & static scanning

Downloads go only to approved domains, are size-capped, and are checksum-verified
when a checksum is published. Archives are scanned **without host extraction** and
flagged for: path traversal, symlinks/hardlinks, device nodes, pickle-class
checkpoints, install/build hooks, and archive bombs. `safetensors`/`gguf`/`onnx`
are not flagged. Extraction happens only inside the container.

## Reporting

Please report security issues privately to the repository owner rather than in a
public issue.
