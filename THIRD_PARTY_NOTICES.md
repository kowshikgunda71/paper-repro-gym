# Third-Party Notices

`paper-repro-gym` itself uses **only the Python standard library** — it declares
no third-party Python dependencies.

At runtime it invokes:

- **Docker** (the `docker` CLI) — used to run reproductions in a locked-down
  container. Docker is not distributed with this project; install it separately.
- A **base container image** you choose per experiment (e.g. `python:3.12-alpine`
  in the bundled example). Such images carry their own licenses and third-party
  components; this project does not redistribute them.

Any paper artifact (code, data, model, checkpoint) reproduced with this tool is
governed by **its own license**, not this repository's. This tool never
redistributes those artifacts.
