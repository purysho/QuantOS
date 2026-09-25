#!/bin/sh
# Builds the QuantOS live-USB ISO inside a Debian container,
# so any Linux machine with Docker (or Podman) can build it.
#
#   packaging/live-usb/build.sh                 # free-software-only image
#   FC_FIRMWARE=1 packaging/live-usb/build.sh   # + non-free firmware (more Wi-Fi/GPUs)
#   FC_SOURCE=true packaging/live-usb/build.sh  # also build the matching source ISO
#   FC_EXTRA_CA=/path/ca.pem ...                # behind a TLS-intercepting proxy (build only;
#                                               # the CA is removed before the image is sealed)
#
# Output: packaging/live-usb/build/quantos-<version>-amd64.iso (+ .sha256)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="$HERE/build"
ENGINE="${CONTAINER_ENGINE:-$(command -v docker || command -v podman)}"
DEBIAN_IMAGE="${DEBIAN_IMAGE:-docker.io/library/debian:trixie}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml")"

[ -n "$ENGINE" ] || { echo "Docker or Podman is required"; exit 1; }
if ! git -C "$REPO" diff --quiet HEAD -- 2>/dev/null; then
  echo "note: uncommitted changes are NOT included; the image is built from HEAD ($(git -C "$REPO" rev-parse --short HEAD))"
fi

rm -rf "$BUILD"; mkdir -p "$BUILD"
cp -r "$HERE/auto" "$HERE/config" "$BUILD/"
mkdir -p "$BUILD/config/includes.chroot/opt/quantos/src"
git -C "$REPO" archive HEAD | tar -x -C "$BUILD/config/includes.chroot/opt/quantos/src"
git -C "$REPO" rev-parse HEAD > "$BUILD/config/includes.chroot/opt/quantos/REVISION"

"$ENGINE" run --rm --privileged \
  -e FC_FIRMWARE="${FC_FIRMWARE:-0}" -e FC_SOURCE="${FC_SOURCE:-false}" \
  ${FC_EXTRA_CA:+-v "$FC_EXTRA_CA:/usr/local/share/ca-certificates/fc-extra.crt:ro"} \
  -v "$BUILD:/build" -w /build "$DEBIAN_IMAGE" sh -euc '
    apt-get update -qq
    apt-get install -y -qq live-build ca-certificates >/dev/null
    if [ -f /usr/local/share/ca-certificates/fc-extra.crt ]; then
      update-ca-certificates >/dev/null
      mkdir -p config/includes.chroot/usr/local/share/ca-certificates
      mkdir -p config/hooks/normal
      cp /usr/local/share/ca-certificates/fc-extra.crt /tmp/fc-extra.crt
      printf "#!/bin/sh\nupdate-ca-certificates\nexport SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt\n" > config/hooks/normal/0100-extra-ca.hook.chroot
      chmod +x config/hooks/normal/0100-extra-ca.hook.chroot
      cp /tmp/fc-extra.crt config/includes.chroot/usr/local/share/ca-certificates/
      # Build-time only: removed again before the image is sealed.
      printf "#!/bin/sh\nrm -f /usr/local/share/ca-certificates/fc-extra.crt\nupdate-ca-certificates --fresh\n" > config/hooks/normal/9990-remove-extra-ca.hook.chroot
      chmod +x config/hooks/normal/9990-remove-extra-ca.hook.chroot
    fi
    lb clean --purge >/dev/null 2>&1 || true
    lb config
    lb build
  '

ISO="$BUILD/quantos-$VERSION-amd64.iso"
mv "$BUILD"/live-image-amd64.hybrid.iso "$ISO"
( cd "$BUILD" && sha256sum "$(basename "$ISO")" > "$(basename "$ISO").sha256" )
cp "$HERE/config/includes.chroot/usr/local/bin/make-quantos-usb" "$BUILD/"
echo
echo "Built $ISO"
echo "Write it with encrypted persistence:  sudo $BUILD/make-quantos-usb $ISO /dev/sdX"
