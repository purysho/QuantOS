# Stage 20 — Container Image and Live USB

## Container (`Containerfile`, `compose.yaml`)

- **Build:** a multi-stage build on the official `python:3.12-slim-trixie` image.
  - `uv sync --locked --no-dev`, with the ORE extra on x86-64;
  - Perspective vendored and integrity-verified at build time;
  - image labels declare Apache-2.0.
- **Runtime:**
  - a non-root user (uid 10001), with `tini` as PID 1;
  - `QUANTOS_HOME=/quantos` on a named volume;
  - offline terminal assets in `/opt/quantos/vendor`.
- **Compose:**
  - `quantos` runs one-off commands;
  - `terminal` serves the read-only terminal;
  - both use a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`;
  - the terminal port is published on the host's **127.0.0.1 only**.
- **In-container bind:** the server may bind 0.0.0.0 only when `QUANTOS_CONTAINER=1` *and* it is really inside a container (`/.dockerenv` or `/run/.containerenv`). Everywhere else, a non-loopback bind is refused.
- **Build options:**
  - `BASE_IMAGE` can point to an official mirror (`mirror.gcr.io/library/…`, `public.ecr.aws/docker/library/…`) when Docker Hub rate-limits;
  - `--secret id=ca,src=…` supplies a proxy CA for the build only.
- **Verified here:**
  - the image builds in about 2.5 minutes and is 1.96 GB;
  - `setup`, `doctor --online` and a full `daily` run pass in a locked-down container;
  - the terminal service passes the browser smoke check with every CDN request blocked;
  - binding without the container flag is refused.

## Live USB (`packaging/live-usb/`)

- Debian 13 live-build: an Xfce desktop, Firefox ESR, and `iso-hybrid` for BIOS and UEFI.
- First Current is installed from HEAD with locked dependencies. The image runs its own license and keyless/runbook tests during the build.
- An encrypted LUKS2 persistence partition (`/home union`) is created by `make-first-current-usb`, which requires typing `YES`.
- There is a first-login welcome/setup flow, a systemd user terminal service (it creates an export if none exists), a weekday daily timer, and desktop launchers.
- The default archive area is `main` only, so the image is 100% free software. `FC_FIRMWARE=1` adds non-free firmware for broader hardware support.
- `FC_SOURCE=true` builds the matching source ISO, which is needed when publishing an ISO (GPL).

See `packaging/live-usb/README.md` for the user-facing instructions.
