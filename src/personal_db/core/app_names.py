"""Resolve macOS bundle IDs to friendly app names.

Resolution tiers (mirrors mosspath's ScreenTimeAppNameResolver.swift):
  1. Hardcoded overrides  - iOS/macOS system apps not covered by mdfind or iTunes
  2. mdfind + Info.plist  - any installed Mac app (no network)
  3. JSON cache           - persists results (and negative results) across runs
  4. iTunes lookup API    - https://itunes.apple.com/lookup?bundleId=...
  5. CamelCase fallback   - last dot segment, spaces inserted before capitals
"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Tier 1: hardcoded overrides
# ---------------------------------------------------------------------------

_SYSTEM_OVERRIDES: dict[str, str] = {
    # iOS system apps (not in NSWorkspace / iTunes)
    "com.apple.springboard": "Home Screen",
    "com.apple.Preferences": "Settings",
    "com.apple.mobilesafari": "Safari",
    "com.apple.MobileSMS": "Messages",
    "com.apple.mobileslideshow": "Photos",
    "com.apple.mobilemail": "Mail",
    "com.apple.mobilecal": "Calendar",
    "com.apple.mobilephone": "Phone",
    "com.apple.camera": "Camera",
    "com.apple.weather": "Weather",
    "com.apple.AppStore": "App Store",
    "com.apple.Health": "Health",
    "com.apple.Fitness": "Fitness",
    "com.apple.Maps": "Maps",
    # macOS analogues commonly seen in screen_time data
    "com.apple.finder": "Finder",
    "com.apple.dock": "Dock",
    "com.apple.SetupAssistant": "Setup Assistant",
    "com.apple.loginwindow": "Login Window",
    "com.apple.systempreferences": "System Settings",
}

# ---------------------------------------------------------------------------
# Process-lifetime in-memory cache (used when no cache_path is given)
# ---------------------------------------------------------------------------

_PROCESS_CACHE: dict[str, str | None] = {}

# ---------------------------------------------------------------------------
# Tier 2: mdfind + Info.plist
# ---------------------------------------------------------------------------


def _name_from_local_app(bundle_id: str) -> str | None:
    """Find a .app via Spotlight, read its Info.plist for the display name."""
    try:
        r = subprocess.run(
            ["mdfind", f'kMDItemCFBundleIdentifier == "{bundle_id}"'],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    paths = [p for p in r.stdout.splitlines() if p.endswith(".app")]
    if not paths:
        return None

    info = Path(paths[0]) / "Contents" / "Info.plist"
    if not info.exists():
        return None

    try:
        with info.open("rb") as f:
            data = plistlib.load(f)
    except Exception:
        return None

    return data.get("CFBundleDisplayName") or data.get("CFBundleName")


# ---------------------------------------------------------------------------
# Tier 4: iTunes lookup API
# ---------------------------------------------------------------------------

_ITUNES_STOREFRONTS = ("us", "gb", "cn", "jp")


def _name_from_itunes(bundle_id: str) -> str | None:
    """Query the iTunes lookup API across several storefronts."""
    for country in _ITUNES_STOREFRONTS:
        url = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country={country}"
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            payload: Any = resp.json()
            if payload.get("resultCount", 0) > 0:
                results = payload.get("results", [])
                if results:
                    name: str | None = results[0].get("trackName")
                    if name:
                        return name
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Tier 5: CamelCase / dot-suffix fallback
# ---------------------------------------------------------------------------


def _fallback_name(bundle_id: str) -> str:
    """Derive a human-readable name from the last dot segment.

    Rules:
    - Take the last segment after splitting on '.'.
    - If it contains uppercase letters, insert spaces before each capital
      that follows a lowercase letter (camelCase split).
    - If the segment is all lowercase/digits (no uppercase), return it as-is
      (e.g. a gibberish token like '230313mzl4w4u92').  In that case the
      caller gets something like 'com.todesktop.230313mzl4w4u92' back
      unchanged — better than a misleading pretty-printed name.
    - Empty segment → return the full bundle_id unchanged.
    """
    segments = bundle_id.split(".")
    last = segments[-1] if segments else ""
    if not last:
        return bundle_id

    # If no uppercase at all, the segment is likely a random token — return
    # the whole bundle_id so callers can still identify it.
    if last == last.lower():
        return bundle_id

    # CamelCase split: insert a space before an uppercase letter that
    # follows a lowercase letter or digit.
    result = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", last)
    return result


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _load_cache(path: Path) -> dict[str, str | None]:
    """Load the JSON cache from disk; return empty dict on any error."""
    try:
        with path.open() as f:
            raw: dict[str, Any] = json.load(f)
        # JSON null → Python None
        return {k: (v if isinstance(v, str) else None) for k, v in raw.items()}
    except Exception:
        return {}


def _save_cache(path: Path, data: dict[str, str | None]) -> None:
    """Persist the cache to disk; silently ignore errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            # Sort keys for stable diffs; use null for None values.
            json.dump(data, f, indent=2, sort_keys=True, default=lambda x: None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_app_name(bundle_id: str, *, cache_path: Path | None = None) -> str:
    """Return a friendly app name for a macOS bundle identifier.

    Tiers: hardcoded overrides → mdfind/Info.plist → JSON cache → iTunes API → fallback.

    Args:
        bundle_id:  The macOS bundle identifier, e.g. ``"com.apple.Safari"``.
        cache_path: Path to a JSON file used for cross-process caching.
                    Defaults to an in-memory dict that persists for the
                    lifetime of the current process only.
    """
    # --- Tier 1: hardcoded overrides ---
    if bundle_id in _SYSTEM_OVERRIDES:
        return _SYSTEM_OVERRIDES[bundle_id]

    # --- Select the backing cache (file or in-memory) ---
    cache = _load_cache(cache_path) if cache_path is not None else _PROCESS_CACHE

    # --- Tier 3: JSON / in-memory cache (checked before mdfind to avoid
    #     re-running Spotlight for already-resolved or already-failed IDs) ---
    if bundle_id in cache:
        cached = cache[bundle_id]
        # None means we already tried all tiers → use fallback
        return cached if cached is not None else _fallback_name(bundle_id)

    # --- Tier 2: installed Mac app via mdfind + Info.plist ---
    local_name = _name_from_local_app(bundle_id)
    if local_name:
        return local_name

    # --- Tier 4: iTunes API ---
    itunes_name = _name_from_itunes(bundle_id)

    # Store result (including None for negative cache)
    cache[bundle_id] = itunes_name
    if cache_path is not None:
        _save_cache(cache_path, cache)
    # _PROCESS_CACHE is mutated in-place when cache_path is None

    if itunes_name:
        return itunes_name

    # --- Tier 5: fallback ---
    return _fallback_name(bundle_id)
