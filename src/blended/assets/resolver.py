"""Asset resolution and caching. Host side.

Maps an asset request to a concrete, normalized `.blend` fragment, keyed by content hash so
identical inputs never rebuild. The ingestion itself happens inside Blender
(`blended_backend.ingest`) — this module decides *whether* it needs to happen at all.

Tier routing per ARCHITECTURE 5: the best available source wins, and raster tracing is a
lossy fallback that is never chosen when a vector source exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from blended.errors import BlendedError

#: Suffix to ingest tier. Ordered best-to-worst; see ARCHITECTURE 5.
TIER_BY_SUFFIX = {
    ".svg": "A",
    ".ai": "A",
    ".pdf": "A",
    ".ttf": "B",
    ".otf": "B",
    ".glb": "C",
    ".gltf": "C",
    ".obj": "C",
    ".fbx": "C",
    ".png": "E",
    ".jpg": "E",
    ".jpeg": "E",
}

#: Tier E goes through raster tracing, which discards curve fidelity.
LOSSY_TIERS = {"E"}

#: Tiers with an implementation today. Everything else is planned, not silently broken.
IMPLEMENTED_TIERS = {"A"}


class AssetError(BlendedError):
    code = "ASSET_UNRESOLVED"


@dataclass(frozen=True)
class AssetRequest:
    source: Path
    name: str
    params: dict = field(default_factory=dict)

    @property
    def tier(self) -> str:
        tier = TIER_BY_SUFFIX.get(self.source.suffix.lower())
        if tier is None:
            raise AssetError(
                f"Don't know how to ingest {self.source.suffix!r} ({self.source.name}).",
                hint=f"Supported: {', '.join(sorted(TIER_BY_SUFFIX))}",
            )
        return tier

    @property
    def lossy(self) -> bool:
        return self.tier in LOSSY_TIERS

    def validate(self) -> None:
        if not self.source.exists():
            raise AssetError(f"Asset source not found: {self.source}")
        tier = self.tier
        if tier not in IMPLEMENTED_TIERS:
            raise AssetError(
                f"{self.source.name} is ingest tier {tier}, which is not implemented yet.",
                hint=(
                    f"Implemented: tier {', '.join(sorted(IMPLEMENTED_TIERS))} "
                    "(vector sources). See ARCHITECTURE 5."
                ),
            )

    def cache_key(self) -> str:
        """Hash of the source *contents* plus every parameter that affects geometry.

        Contents rather than path or mtime, so moving or touching a file is free and editing
        it is correctly a miss.
        """
        digest = hashlib.sha256()
        digest.update(self.source.read_bytes())
        digest.update(json.dumps(self.params, sort_keys=True).encode())
        digest.update(self.name.encode())
        return digest.hexdigest()[:16]


@dataclass
class ResolvedAsset:
    request: AssetRequest
    blend_path: Path
    manifest_path: Path
    cached: bool

    @property
    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())


class AssetCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def paths_for(self, request: AssetRequest) -> tuple[Path, Path]:
        key = request.cache_key()
        directory = self.root / f"{request.name}-{key}"
        return directory / "asset.blend", directory / "manifest.json"

    def lookup(self, request: AssetRequest) -> ResolvedAsset | None:
        blend_path, manifest_path = self.paths_for(request)
        if blend_path.exists() and manifest_path.exists():
            return ResolvedAsset(request, blend_path, manifest_path, cached=True)
        return None

    def store(self, request: AssetRequest, manifest: dict) -> ResolvedAsset:
        blend_path, manifest_path = self.paths_for(request)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(manifest)
        payload["provenance"] = {
            "source": str(request.source),
            "tier": request.tier,
            "lossy": request.lossy,
            "cache_key": request.cache_key(),
        }
        manifest_path.write_text(json.dumps(payload, indent=2))
        return ResolvedAsset(request, blend_path, manifest_path, cached=False)
