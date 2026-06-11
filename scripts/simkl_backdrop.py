#!/usr/bin/env python3
"""
Generate hero backdrop images from Simkl watching / plantowatch lists.

Fetches TV shows + anime for the authenticated Simkl user, enriches each
item with TMDB backdrop art, then renders two webp files using the
tilted-grid renderer from backdrop.py (unchanged).

Required environment variables:
  TMDB_API_KEY          TMDB v3 API key
  SIMKL_CLIENT_ID       Simkl application client ID
  SIMKL_ACCESS_TOKEN    Simkl OAuth Bearer token  (run get_simkl_token.py once)

Outputs:
  backdrops/watchlist.webp     — shows/anime with Simkl status "watching"
  backdrops/plantowatch.webp   — shows/anime with Simkl status "plantowatch"
"""

import os
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT_DIR = REPO_ROOT / "backdrops"

sys.path.insert(0, str(SCRIPT_DIR))
from backdrop import (
    FOCUS_X,
    FOCUS_Y,
    SIZE_PRESETS,
    apply_gradient,
    build_tilted_grid,
    default_accent_for_label,
    ensure_minimum_tiles,
    fetch_tile_image,
    resolve_quality_settings,
    save_output,
)

SIMKL_API = "https://api.simkl.com"
TMDB_API = "https://api.themoviedb.org/3"
SIMKL_CONTENT_TYPES = ("shows", "anime")
OUTPUT_SIZE = "1080p"
MAX_ITEMS = 60
TMDB_RATE_DELAY = 0.25


def get_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Error: environment variable {name} is not set.")
        sys.exit(1)
    return value


def simkl_get(path, client_id, access_token, params=None):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "simkl-api-key": client_id,
    }
    resp = requests.get(
        f"{SIMKL_API}{path}",
        headers=headers,
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_simkl_items(client_id, access_token, status):
    """
    Fetch TV shows + anime from Simkl for the given status.
    Returns a deduplicated list of (tmdb_id, title) tuples.
    """
    seen_tmdb_ids = set()
    results = []

    for content_type in SIMKL_CONTENT_TYPES:
        print(f"  Fetching Simkl {content_type} (status={status!r})...")
        try:
            data = simkl_get(
                f"/sync/all-items/{content_type}",
                client_id,
                access_token,
                params={"extended": "ids"},
            )
        except requests.HTTPError as exc:
            print(f"  ! Simkl {content_type} request failed: {exc}")
            continue

        # API returns either a bare list or {"shows": [...]} / {"anime": [...]}
        if isinstance(data, dict):
            entries = data.get(content_type) or data.get("shows") or []
        else:
            entries = data if isinstance(data, list) else []

        for entry in entries:
            if entry.get("status") != status:
                continue
            # Inner show/anime object uses "show" as key for both content types
            show_obj = entry.get("show") or entry.get("anime") or {}
            ids = show_obj.get("ids", {})
            raw_tmdb = ids.get("tmdb")
            if not raw_tmdb:
                continue
            try:
                tmdb_id = int(raw_tmdb)
            except (ValueError, TypeError):
                continue
            if tmdb_id in seen_tmdb_ids:
                continue
            seen_tmdb_ids.add(tmdb_id)
            results.append((tmdb_id, show_obj.get("title", "?")))

    return results


def tmdb_tv_details(tmdb_id, api_key):
    """Return a TMDB item dict for backdrop rendering, or None on failure."""
    try:
        resp = requests.get(
            f"{TMDB_API}/tv/{tmdb_id}",
            params={"api_key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        backdrop = data.get("backdrop_path")
        if not backdrop:
            return None
        return {
            "id": tmdb_id,
            "backdrop_path": backdrop,
            "original_language": data.get("original_language", "en"),
            "name": data.get("name") or "?",
        }
    except Exception as exc:
        print(f"  ! TMDB lookup failed for tv/{tmdb_id}: {exc}")
        return None


def render(tmdb_items, tmdb_api_key, output_webp_path, label):
    """Download tiles and render the tilted-grid backdrop."""
    print(f"\nDownloading tile images ({len(tmdb_items)} items)...")
    tile_images = []
    for i, (media_type, item) in enumerate(tmdb_items, 1):
        print(f"  [{i:02d}/{len(tmdb_items)}] {item.get('name', '?')[:50]}")
        image, _ = fetch_tile_image(media_type, item, tmdb_api_key, None, "en")
        if image:
            tile_images.append(image)

    if not tile_images:
        raise RuntimeError(f"No backdrop images downloaded for {label!r}.")

    tile_images = ensure_minimum_tiles(tile_images, 12)

    accent = default_accent_for_label(label)
    quality_settings = resolve_quality_settings(profile="compressed")
    width, height, scale = SIZE_PRESETS[OUTPUT_SIZE]

    print(f"\nCompositing {OUTPUT_SIZE} ({width}x{height})...")
    canvas = build_tilted_grid(
        tile_images, width, height, scale=scale, focus_x=FOCUS_X, focus_y=FOCUS_Y
    )
    canvas = apply_gradient(canvas, accent)

    # save_output always writes a .jpg then derives a .webp sidecar from it
    webp_path = Path(output_webp_path)
    jpg_path = webp_path.with_suffix(".jpg")
    save_output(canvas, jpg_path, quality_settings)
    jpg_path.unlink(missing_ok=True)
    print(f"  Final output: {webp_path}")


def process_list(label, status, output_filename, client_id, access_token, tmdb_api_key):
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}\n")

    simkl_items = fetch_simkl_items(client_id, access_token, status)
    print(f"  {len(simkl_items)} item(s) found in Simkl {status!r} list.\n")
    if not simkl_items:
        print(f"  Nothing to render for {label}. Skipping.")
        return

    capped = simkl_items[:MAX_ITEMS]
    print(f"  Enriching {len(capped)} item(s) with TMDB details...")
    tmdb_items = []
    for i, (tmdb_id, title) in enumerate(capped, 1):
        print(f"  [{i:02d}/{len(capped)}] {title[:50]}")
        details = tmdb_tv_details(tmdb_id, tmdb_api_key)
        if details:
            tmdb_items.append(("tv", details))
        time.sleep(TMDB_RATE_DELAY)

    print(f"\n  {len(tmdb_items)} item(s) have usable TMDB backdrops.")
    if not tmdb_items:
        print(f"  No backdrops available. Skipping {label}.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    render(tmdb_items, tmdb_api_key, OUTPUT_DIR / output_filename, label)


def main():
    tmdb_api_key = get_env("TMDB_API_KEY")
    client_id = get_env("SIMKL_CLIENT_ID")
    access_token = get_env("SIMKL_ACCESS_TOKEN")

    process_list(
        label="Watchlist (Watching)",
        status="watching",
        output_filename="watchlist.webp",
        client_id=client_id,
        access_token=access_token,
        tmdb_api_key=tmdb_api_key,
    )
    process_list(
        label="Plan to Watch",
        status="plantowatch",
        output_filename="plantowatch.webp",
        client_id=client_id,
        access_token=access_token,
        tmdb_api_key=tmdb_api_key,
    )

    print("\nAll done.")


if __name__ == "__main__":
    main()
