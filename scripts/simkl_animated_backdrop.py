#!/usr/bin/env python3
"""
Generate looping MP4 animated backdrops from Simkl watching / plantowatch lists.

The tilted card grid drifts diagonally in a seamless 4-second loop.

Same env vars as simkl_backdrop.py:
  TMDB_API_KEY, SIMKL_CLIENT_ID, SIMKL_ACCESS_TOKEN

Outputs:
  videos/watchlist.mp4
  videos/plantowatch.mp4

Requires ffmpeg on PATH (pre-installed on ubuntu-latest GitHub Actions runners).
"""

import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
VIDEO_DIR = REPO_ROOT / "videos"

sys.path.insert(0, str(SCRIPT_DIR))
from backdrop import (
    COLS,
    FOCUS_X,
    FOCUS_Y,
    GAP,
    ROWS,
    SIZE_PRESETS,
    STAGGER,
    TILE_H,
    TILE_W,
    TILT_DEG,
    apply_gradient,
    default_accent_for_label,
    ensure_minimum_tiles,
    fetch_tile_image,
    make_tile,
)

SIMKL_API = "https://api.simkl.com"
TMDB_API = "https://api.themoviedb.org/3"
SIMKL_CONTENT_TYPES = ("shows", "anime")
OUTPUT_SIZE = "4k"
MAX_ITEMS = 60
TMDB_RATE_DELAY = 0.25

FPS = 24
DURATION_S = 4
N_FRAMES = FPS * DURATION_S  # 96


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
    resp = requests.get(f"{SIMKL_API}{path}", headers=headers, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_simkl_items(client_id, access_token, status):
    seen = set()
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
            print(f"  ! Simkl {content_type} failed: {exc}")
            continue
        if isinstance(data, dict):
            entries = data.get(content_type) or data.get("shows") or []
        else:
            entries = data if isinstance(data, list) else []
        for entry in entries:
            if entry.get("status") != status:
                continue
            show_obj = entry.get("show") or entry.get("anime") or {}
            ids = show_obj.get("ids", {})
            raw_tmdb = ids.get("tmdb")
            if not raw_tmdb:
                continue
            try:
                tmdb_id = int(raw_tmdb)
            except (ValueError, TypeError):
                continue
            if tmdb_id in seen:
                continue
            seen.add(tmdb_id)
            results.append((tmdb_id, show_obj.get("title", "?")))
    return results


def tmdb_tv_details(tmdb_id, api_key):
    try:
        resp = requests.get(
            f"{TMDB_API}/tv/{tmdb_id}", params={"api_key": api_key}, timeout=15
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
        print(f"  ! TMDB tv/{tmdb_id}: {exc}")
        return None


def build_rotated_grid(tile_images, canvas_width, canvas_height, scale):
    """
    Build the oversized tilted grid once and return everything needed for animation.
    Returns (rotated_image, paste_x, paste_y, drift_x, drift_y).

    drift_x / drift_y: total paste-position shift over one full loop.  Shifting
    by exactly one cell (width) and half a cell (height) lands on an identical
    region of the repeating tile pattern, producing a seamless loop.
    """
    tile_width = int(TILE_W * scale)
    tile_height = int(TILE_H * scale)
    gap = int(GAP * scale)
    cell_w = tile_width + gap
    cell_h = tile_height + gap

    cols = COLS + 3
    rows = ROWS + 3
    stagger_px = int(STAGGER * cell_w)

    grid_width = cols * cell_w + rows * stagger_px
    grid_height = rows * cell_h
    grid = Image.new("RGBA", (grid_width, grid_height), (0, 0, 0, 0))

    # Focal point — place best tiles here (mirrors build_tilted_grid exactly)
    focal_x = FOCUS_X * grid_width
    focal_y = FOCUS_Y * grid_height
    focal_row = max(0, min(rows - 1, int(focal_y / cell_h)))
    focal_col = max(0, min(cols - 1, int((focal_x - focal_row * stagger_px) / cell_w)))

    cells = [(r, c) for r in range(rows) for c in range(cols)]
    cells.sort(key=lambda pos: abs(pos[0] - focal_row) + abs(pos[1] - focal_col))

    needed = rows * cols
    tile_list = (tile_images * (needed // len(tile_images) + 1))[:needed]

    for idx, (row, col) in enumerate(cells):
        if idx >= len(tile_list):
            break
        x = row * stagger_px + col * cell_w
        y = row * cell_h
        t = make_tile(tile_list[idx], tile_width, tile_height)
        grid.paste(t, (x, y), t)

    print("  Rotating grid...")
    rotated = grid.rotate(TILT_DEG, expand=True, resample=Image.BICUBIC)
    del grid  # free ~338 MB
    rw, rh = rotated.size

    # Replicate the focus-point math from build_tilted_grid
    angle_rad = math.radians(-TILT_DEG)
    pre_cx = FOCUS_X * grid_width - grid_width / 2
    pre_cy = FOCUS_Y * grid_height - grid_height / 2
    rot_cx = pre_cx * math.cos(angle_rad) - pre_cy * math.sin(angle_rad)
    rot_cy = pre_cx * math.sin(angle_rad) + pre_cy * math.cos(angle_rad)
    focus_rx = rw / 2 + rot_cx
    focus_ry = rh / 2 + rot_cy

    paste_x = int(canvas_width / 2 - focus_rx)
    paste_y = int(canvas_height / 2 - focus_ry)

    # One cell width + half cell height = one repeat of the tile pattern
    drift_x = cell_w
    drift_y = cell_h // 2

    return rotated, paste_x, paste_y, drift_x, drift_y


def render_animated(tmdb_items, tmdb_api_key, output_mp4_path, label):
    """Download tiles, build grid, render frames, encode MP4."""
    print(f"\nDownloading tile images ({len(tmdb_items)} items)...")
    tile_images = []
    for i, (media_type, item) in enumerate(tmdb_items, 1):
        print(f"  [{i:02d}/{len(tmdb_items)}] {item.get('name', '?')[:50]}")
        image, _ = fetch_tile_image(media_type, item, tmdb_api_key, None, "en")
        if image:
            tile_images.append(image)

    if not tile_images:
        raise RuntimeError(f"No backdrop images for {label!r}.")

    tile_images = ensure_minimum_tiles(tile_images, 12)

    canvas_width, canvas_height, scale = SIZE_PRESETS[OUTPUT_SIZE]
    print(f"\nBuilding rotated grid ({OUTPUT_SIZE} {canvas_width}x{canvas_height})...")
    rotated, paste_x, paste_y, drift_x, drift_y = build_rotated_grid(
        tile_images, canvas_width, canvas_height, scale
    )
    print(f"  Grid built. Drift per loop: {drift_x}x{drift_y} px.")

    # Pre-compute the gradient overlay ONCE — applying it 96× would be very slow.
    # apply_gradient composites layers over the canvas; since those layers don't
    # depend on canvas content, we can pre-merge them on a transparent canvas and
    # alpha_composite onto each frame instead.
    print("  Pre-computing gradient overlay...")
    accent = default_accent_for_label(label)
    empty = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    gradient_overlay = apply_gradient(empty, accent)
    del empty

    bg = (10, 10, 12, 255)
    output_path = Path(output_mp4_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nRendering {N_FRAMES} frames at {FPS}fps ({DURATION_S}s loop)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(N_FRAMES):
            offset_x = int(drift_x * i / N_FRAMES)
            offset_y = int(drift_y * i / N_FRAMES)

            canvas = Image.new("RGBA", (canvas_width, canvas_height), bg)
            canvas.paste(rotated, (paste_x + offset_x, paste_y + offset_y), rotated)
            frame = Image.alpha_composite(canvas, gradient_overlay)
            frame.convert("RGB").save(Path(tmpdir) / f"f_{i:03d}.png", "PNG")

            if (i + 1) % FPS == 0:
                print(f"  Frame {i + 1}/{N_FRAMES}")

        print("\nEncoding MP4...")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-framerate", str(FPS),
                "-i", str(Path(tmpdir) / "f_%03d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "18",
                "-movflags", "+faststart",
                str(output_path),
            ],
            check=True,
        )

    size_mb = output_path.stat().st_size / 1_048_576
    print(f"  Saved {output_path} ({size_mb:.1f} MB)")


def process_list(label, status, output_filename, client_id, access_token, tmdb_api_key):
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}\n")

    simkl_items = fetch_simkl_items(client_id, access_token, status)
    print(f"  {len(simkl_items)} item(s) in Simkl {status!r} list.\n")
    if not simkl_items:
        print(f"  Skipping {label}: empty list.")
        return

    capped = simkl_items[:MAX_ITEMS]
    print(f"  Enriching {len(capped)} item(s) with TMDB...")
    tmdb_items = []
    for i, (tmdb_id, title) in enumerate(capped, 1):
        print(f"  [{i:02d}/{len(capped)}] {title[:50]}")
        details = tmdb_tv_details(tmdb_id, tmdb_api_key)
        if details:
            tmdb_items.append(("tv", details))
        time.sleep(TMDB_RATE_DELAY)

    print(f"\n  {len(tmdb_items)} item(s) have TMDB backdrops.")
    if not tmdb_items:
        print(f"  Skipping {label}: no backdrops.")
        return

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    render_animated(tmdb_items, tmdb_api_key, VIDEO_DIR / output_filename, label)


def main():
    tmdb_api_key = get_env("TMDB_API_KEY")
    client_id = get_env("SIMKL_CLIENT_ID")
    access_token = get_env("SIMKL_ACCESS_TOKEN")

    process_list(
        label="Watchlist (Watching)",
        status="watching",
        output_filename="watchlist.mp4",
        client_id=client_id,
        access_token=access_token,
        tmdb_api_key=tmdb_api_key,
    )
    process_list(
        label="Plan to Watch",
        status="plantowatch",
        output_filename="plantowatch.mp4",
        client_id=client_id,
        access_token=access_token,
        tmdb_api_key=tmdb_api_key,
    )

    print("\nAll done.")


if __name__ == "__main__":
    main()
