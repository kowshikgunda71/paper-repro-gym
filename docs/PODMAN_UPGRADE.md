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
