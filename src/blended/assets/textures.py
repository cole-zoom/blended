"""PBR texture sets from Poly Haven. Host side.

The "don't reinvent the wheel" path for photoreal materials: Poly Haven publishes CC0 scanned
PBR sets, so there is nothing to author and nothing to attribute.

This runs on the **host**, never in Blender — the backend has no network and no third-party
packages. The host downloads and caches; the backend is handed local file paths. Stdlib `urllib`
rather than `requests` so no dependency is added for four HTTP GETs.

Cache layout, keyed by asset and resolution so several sets can coexist:

    <cache>/polyhaven/<asset>_<resolution>/
        diffuse.jpg  normal.jpg  roughness.jpg  ao.jpg  displacement.jpg
        manifest.json
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import certifi

from blended.errors import BlendedError

#: python.org builds on macOS ship without a CA bundle, so the default SSL context fails to
#: verify any HTTPS certificate ("unable to get local issuer certificate") even though `curl`
#: works fine off the system store. certifi supplies the bundle. Verification stays ON —
#: disabling it would silence the error by removing the security.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

API = "https://api.polyhaven.com"

#: Our role names mapped to Poly Haven's map names, in preference order.
#: `nor_gl` is the OpenGL-convention normal map — the one Blender's Normal Map node expects.
#: `nor_dx` is DirectX convention with an inverted green channel and would light backwards.
MAP_ALIASES = {
    "diffuse": ("Diffuse", "diff", "albedo"),
    "normal": ("nor_gl",),
    "roughness": ("Rough", "rough"),
    "ao": ("AO", "ao"),
    "displacement": ("Displacement", "disp"),
}

#: Maps we cannot build a sensible material without.
REQUIRED = ("diffuse", "normal", "roughness")

TIMEOUT = 60

#: Poly Haven returns 403 to urllib's default User-Agent ("Python-urllib/3.x"). Identify the
#: client properly rather than spoofing a browser.
USER_AGENT = "blended/0.1 (+https://github.com/blended) python-urllib"


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=TIMEOUT, context=_SSL_CONTEXT)


class TextureError(BlendedError):
    code = "TEXTURE_FETCH_FAILED"


@dataclass(frozen=True)
class TextureSet:
    asset: str
    resolution: str
    directory: Path
    maps: dict[str, str]

    def path(self, role: str) -> str | None:
        name = self.maps.get(role)
        return str(self.directory / name) if name else None

    def as_dict(self) -> dict:
        return {
            "asset": self.asset,
            "resolution": self.resolution,
            "maps": {role: self.path(role) for role in self.maps},
        }


def _get_json(url: str) -> dict:
    try:
        with _open(url) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TextureError(
            f"Could not reach Poly Haven ({url}): {exc}",
            hint="Textures need network access. Use the procedural material offline.",
        ) from exc


def _pick(files: dict, role: str, resolution: str, fmt: str = "jpg") -> tuple[str, str] | None:
    """Resolve one role to a (url, filename) pair, or None if unavailable."""
    for candidate in MAP_ALIASES[role]:
        entry = files.get(candidate)
        if not entry:
            continue
        by_res = entry.get(resolution) or entry.get("2k") or next(iter(entry.values()), None)
        if not by_res:
            continue
        chosen = by_res.get(fmt) or next(iter(by_res.values()), None)
        if chosen and chosen.get("url"):
            suffix = Path(chosen["url"]).suffix or ".jpg"
            return chosen["url"], f"{role}{suffix}"
    return None


def fetch(asset: str, cache_root: Path, *, resolution: str = "2k",
          roles: tuple[str, ...] = tuple(MAP_ALIASES)) -> TextureSet:
    """Download a texture set, or return the cached copy.

    The manifest doubles as the cache marker: it is written only after every file has landed,
    so an interrupted download is a miss rather than a half-populated set that fails later
    inside Blender.
    """
    # Absolute, always. These paths are handed to Blender, which re-resolves relative paths
    # against the saved .blend's directory rather than the working directory — so a relative
    # path silently becomes wrong the moment a .blend is written somewhere else, and the
    # texture renders as magenta with no error anywhere.
    directory = Path(cache_root).resolve() / "polyhaven" / f"{asset}_{resolution}"
    manifest_path = directory / "manifest.json"

    if manifest_path.exists():
        stored = json.loads(manifest_path.read_text())
        if all((directory / name).exists() for name in stored["maps"].values()):
            return TextureSet(asset, resolution, directory, stored["maps"])

    files = _get_json(f"{API}/files/{asset}")
    directory.mkdir(parents=True, exist_ok=True)

    maps: dict[str, str] = {}
    for role in roles:
        picked = _pick(files, role, resolution)
        if picked is None:
            continue
        url, filename = picked
        destination = directory / filename
        if not destination.exists():
            try:
                # urlretrieve supports neither an ssl context nor custom headers.
                with _open(url) as response:
                    destination.write_bytes(response.read())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                destination.unlink(missing_ok=True)  # never leave a truncated map cached
                raise TextureError(f"Failed downloading {role} for {asset}: {exc}") from exc
        maps[role] = filename

    missing = [role for role in REQUIRED if role not in maps]
    if missing:
        raise TextureError(
            f"Poly Haven asset {asset!r} is missing required maps: {', '.join(missing)}",
            hint=f"Available: {', '.join(sorted(files))}",
        )

    manifest_path.write_text(json.dumps(
        {"asset": asset, "resolution": resolution, "maps": maps, "source": "polyhaven",
         "license": "CC0"},
        indent=2,
    ))
    return TextureSet(asset, resolution, directory, maps)


def fetch_hdri(asset: str, cache_root: Path, *, resolution: str = "2k") -> str:
    """Download an HDRI environment map and return its absolute path.

    HDRIs use a different shape to texture sets: a single `hdri` key rather than per-role maps.
    `.hdr` is chosen over `.exr` — visually equivalent for lighting at these resolutions and
    roughly a third the size (7 MB vs 25 MB at 2k).
    """
    directory = Path(cache_root).resolve() / "polyhaven_hdri"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{asset}_{resolution}.hdr"
    if destination.exists():
        return str(destination)

    files = _get_json(f"{API}/files/{asset}")
    entry = files.get("hdri")
    if not entry:
        raise TextureError(f"{asset!r} is not an HDRI (keys: {', '.join(files)})")
    by_res = entry.get(resolution) or entry.get("2k") or next(iter(entry.values()))
    chosen = by_res.get("hdr") or next(iter(by_res.values()))

    try:
        with _open(chosen["url"]) as response:
            destination.write_bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise TextureError(f"Failed downloading HDRI {asset}: {exc}") from exc
    return str(destination)
