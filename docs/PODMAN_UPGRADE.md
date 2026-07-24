# Upgrading the boundary: rootless Podman

The default runner uses Docker, which is convenient but root-equivalent when the
invoking user is in the `docker` group (a container escape reaches the host).
**Rootless Podman** runs containers as your unprivileged user with a user
namespace, so an escape lands as an unprivileged user, not root — a materially
stronger boundary on a shared host.

## Install (Debian/Ubuntu) — requires sudo

```bash
sudo apt-get update
sudo apt-get install -y podman uidmap slirp4netns

# Enable rootless user namespaces if AppArmor/sysctl blocks them:
echo 'kernel.apparmor_restrict_unprivileged_userns = 0' | \
  sudo tee /etc/sysctl.d/99-rootless-userns.conf
sudo sysctl --system

# Subuid/subgid ranges for your user (if not already present):
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"
podman system migrate
```

Verify rootless works:

```bash
podman run --rm --network=none --user 65534:65534 python:3.12-alpine id
```

## Point the gym at Podman

`podman` is CLI-compatible with the flags the runner uses. In
`src/paper_repro_gym/core.py`, change `SANDBOX_POLICY["runtime"]` to `"podman"`
and replace the two `"docker"` literals in `docker_argv()` with `"podman"` (or
symlink `docker -> podman`). Because the runner already builds the command as an
argv array with `--network=none --cap-drop=ALL --user=65534:65534 --read-only`,
no other change is needed. Changing the policy also changes its hash, which
correctly invalidates any approval signed against the old policy.

## Stronger still

For genuinely untrusted artifacts, prefer a **disposable VM** (e.g. a microVM or
a throwaway cloud instance) or a dedicated host that holds no secrets. Container
isolation shares the host kernel; a VM does not.

## Redlines — hard limits when running untrusted third-party code

These are non-negotiable. The gym enforces the ones marked **[enforced]**; the
rest are operating rules you must not cross.

**[enforced] Never run untrusted code on a root-equivalent boundary.**
`gym preflight` classifies the runtime as `hardened` (rootless podman),
`weak` (docker while you are in the `docker` group, or rootful podman), or
`unknown`. Pass `--require-hardened` to `gym run` for any real paper artifact —
it refuses to start on anything but `hardened`. Docker on a workstation where
your user is in the `docker` group is `weak`: a container escape is a host root
compromise.

**[enforced] The A1 gate binds the runtime.** The runtime is part of the
sandbox policy, so an approval signed for docker will not verify for a podman
run. Switching runtime forces re-approval.

**Never weaken the lockdown to "make it work".** Do not add `--privileged`, do
not mount the container/host socket, do not `--cap-add`, do not switch
`--network=none` to anything but an explicitly approved allowlist, do not run as
root inside, do not disable seccomp/AppArmor. If an artifact "needs" any of
these, that is a finding — treat it as no-go, not a config to loosen.

**Never mount host secrets or the home directory.** Inputs are read-only; only
per-run scratch/output are writable. The runner already refuses to mount the
home dir, `.ssh`, `.env`, `/secrets`, or the docker socket — keep it that way.

**Acquire before you isolate; isolate before you run.** Download only from
approved domains, checksum-verify, and scan **without** host extraction.
Extraction and execution happen only inside the container.

**Never load an untrusted pickle-class checkpoint on the host** (`.pkl`, `.pt`,
`.pth`, `.ckpt`, `.bin`, …). Prefer `safetensors`/`gguf`/`onnx`. Loading a
pickle is arbitrary code execution.

**Auto-route the dangerous domains to a human.** Human-subject, clinical,
biological, malware, surveillance, biometric, or export-controlled work is
never auto-run — it goes to manual review or no-go regardless of feasibility.

**Preflight before every real run:**

```bash
GYM_RUNTIME=podman gym preflight        # expect boundary: hardened
GYM_RUNTIME=podman gym run my_experiment --require-hardened
```

If `preflight` is not `hardened`, stop. Fix the boundary (rootless podman or a
disposable VM) before running — do not override the redline.
