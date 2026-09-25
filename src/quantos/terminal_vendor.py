"""Offline terminal assets: vendors the pinned Perspective build locally.

``quantos terminal vendor`` downloads the four FINOS Perspective 3.8.0
tarballs from the npm registry and checks each one against its frozen npm
``sha512`` integrity. It extracts only the runtime files and the license
texts into ``$QUANTOS_HOME/vendor/npm/@finos/<pkg>@3.8.0/``, the same paths
jsDelivr serves. After that, terminal exports use the local copy, so the
terminal works with no internet connection. The browser still enforces the
SRI hashes in ``index.html``, so a vendored file that differs from the
pinned CDN file is refused.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import tarfile
from pathlib import Path
from typing import Callable

from .home import home_dir
from .terminal import PERSPECTIVE_VERSION

VENDOR_DIR = "vendor"
CDN_PREFIX = "https://cdn.jsdelivr.net/npm/"
LOCAL_PREFIX = "./vendor/npm/"

# npm registry integrity (sha512 of the published tarball), frozen at pin time.
PERSPECTIVE_TARBALLS: dict[str, str] = {
    "perspective": "sha512-9pot9YJq1RDdIRJlffp97ktbd0Sxk74mnoSOcf6U/luWceyEnfceQYHwJqUPR1IMNb9H1ZC6NQTbU4WRUT3hAQ==",
    "perspective-viewer": "sha512-u5dtQw4NzlbMOPZgvi+Tr1p0ZDFoYLne9tV99yEGIEqhQFWG3Ke2RL/562RjDm7o2Vk/nPHP+K9RkF/9KceBMg==",
    "perspective-viewer-datagrid": "sha512-iqe4DDXYvb2jW9PQrRSliicFvhMe36OlGjZUgouQJrtuQSlgqTWQFsp9/4k8TTnhhgVhOyVfkSjX7bykXfSuvw==",
    "perspective-viewer-d3fc": "sha512-uxgzgwnEY1VvrJ5EE+hZHELbhfNFrvpAR28i2GMIXqjDv2ce3TkTOgNZKyKF67yPxG+RW1as4M+npAZieWHF1g==",
}
_KEEP_PREFIXES = ("dist/cdn/", "dist/wasm/", "dist/css/")
_KEEP_FILES = {"package.json", "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "NOTICE.md"}

Fetcher = Callable[[str], bytes]


class VendorError(ValueError):
    pass


def vendor_root(home: Path | None = None) -> Path:
    return (home or home_dir()) / VENDOR_DIR / "npm" / "@finos"


def vendored_assets_dir(home: Path | None = None) -> Path | None:
    root = vendor_root(home)
    complete = all((root / f"{pkg}@{PERSPECTIVE_VERSION}" / ".verified").is_file() for pkg in PERSPECTIVE_TARBALLS)
    return root if complete else None


def _default_fetch(url: str) -> bytes:
    import requests

    from .security import guarded

    guarded(url, "terminal-vendor")
    response = requests.get(url, timeout=120)
    if response.status_code != 200:
        raise VendorError(f"download failed with HTTP {response.status_code}: {url}")
    return response.content


def vendor_perspective(*, home: Path | None = None, fetch: Fetcher | None = None,
                       tarballs: dict[str, str] | None = None) -> Path:
    fetch = fetch or _default_fetch
    root = vendor_root(home)
    for package, integrity in (tarballs or PERSPECTIVE_TARBALLS).items():
        url = f"https://registry.npmjs.org/@finos/{package}/-/{package}-{PERSPECTIVE_VERSION}.tgz"
        body = fetch(url)
        digest = "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()
        if digest != integrity:
            raise VendorError(f"{package}: tarball integrity mismatch (expected {integrity}, got {digest})")
        target = root / f"{package}@{PERSPECTIVE_VERSION}"
        staging = target.with_name(target.name + ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                name = member.name.split("/", 1)[1] if "/" in member.name else member.name
                if ".." in Path(name).parts or name.startswith("/"):
                    raise VendorError(f"{package}: unsafe path in tarball: {member.name}")
                if not (name.startswith(_KEEP_PREFIXES) or name in _KEEP_FILES) or name.endswith(".map"):
                    continue
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                destination.write_bytes(source.read())
                os.chmod(destination, 0o644)
        if not any(staging.glob("LICENSE*")):
            raise VendorError(f"{package}: tarball has no license file; refusing to redistribute")
        (staging / ".verified").write_text(integrity + "\n")
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)
    return root


def localize(text: str) -> str:
    """Rewrites pinned CDN URLs to the vendored copy inside an export."""

    return text.replace(CDN_PREFIX, LOCAL_PREFIX)


def install_into_export(out_dir: Path, *, home: Path | None = None) -> bool:
    source = vendored_assets_dir(home)
    if source is None:
        return False
    target = out_dir / "vendor" / "npm" / "@finos"
    shutil.rmtree(out_dir / "vendor", ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.staging", ".verified"))
    for name in ("index.html", "terminal.js"):
        path = out_dir / name
        path.write_text(localize(path.read_text(encoding="utf-8")), encoding="utf-8")
    return True
