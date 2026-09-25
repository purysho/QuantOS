# QuantOS — Live USB

A bootable Debian 13 ("trixie") system with QuantOS preinstalled:
- an Xfce desktop, Firefox ESR, and the read-only terminal;
- a first-login setup wizard;
- a daily update timer;
- an **encrypted persistence partition** for your home folder.

## Build the ISO

This needs any x86-64 Linux machine with Docker or Podman, and about 15 GB of free disk space.

```bash
packaging/live-usb/build.sh                  # 100% free software (Debian "main" only)
FC_FIRMWARE=1 packaging/live-usb/build.sh    # + non-free firmware: more Wi-Fi cards and GPUs work
FC_SOURCE=true packaging/live-usb/build.sh   # + matching source ISO (needed to publish the ISO)
```

The build uses `live-build` in a privileged Debian container.
- It installs QuantOS from the repository's **HEAD** commit, with exactly the locked dependencies (`uv sync --locked`, uv pinned by hash).
- It vendors Perspective after verifying the npm integrity hashes.
- It **runs the license gate and the keyless/runbook test suites inside the image**, so the build fails if they fail.

Output: `packaging/live-usb/build/quantos-<version>-amd64.iso`, plus a `.sha256` file and the `make-quantos-usb` helper.

## Write a USB stick (16 GB or larger)

```bash
sudo packaging/live-usb/build/make-quantos-usb quantos-<version>-amd64.iso /dev/sdX
```

The script:
1. shows the target device and requires you to type `YES`;
2. writes the ISO;
3. creates a LUKS2-encrypted `persistence` partition in the remaining space, with `/home union`.

At boot, you are asked for the passphrase. Without the partition, the system still works, but nothing survives a reboot.

On Windows or macOS, write the ISO with a tool that copies it byte for byte (for example, balenaEtcher or Rufus in DD mode). That gives a working stick without persistence. To add persistence, run the helper from the live system itself, on a second stick.

## What's on the image

| | |
|---|---|
| QuantOS | `/opt/quantos/venv`, with `quantos`, `quantos-radar` and `quantos-lab` on the `PATH` |
| Offline terminal assets | `/opt/quantos/vendor` (`QUANTOS_VENDOR_DIR`) |
| Your home | `~/QuantOS` (`QUANTOS_HOME`): configuration, `secrets/` (0700) and `data/` |
| Services (systemd user) | `quantos-terminal.service` (loopback only); `quantos-daily.timer` (weekdays 22:30 UTC) |
| Desktop icons | Terminal, Update data, Setup, Health check |
| Licenses | `/usr/share/doc/quantos/` (LICENSE, NOTICE, THIRD_PARTY_NOTICES.md, requirements.lock) |

Live user: `quantos`, with the standard Debian live password `live`.

## Security notes

- The terminal listens on 127.0.0.1 only. There is no remote access or SSH server.
- Persistence is LUKS2-encrypted. API keys are stored as 0600 files inside your encrypted home.
- Behind a TLS-intercepting proxy, pass `FC_EXTRA_CA=/path/ca.pem` to build.sh. The CA is used during the build and removed before the image is sealed.

## Publishing an ISO

A built ISO redistributes Debian packages under their own licenses, including the GPL. Publish the matching source ISO (`FC_SOURCE=true`) alongside it, or point to the exact package versions on snapshot.debian.org. QuantOS itself is Apache-2.0. See `docs/LICENSING.md`.
