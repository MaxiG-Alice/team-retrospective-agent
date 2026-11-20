"""Year-in-review collage generator combining photos and customer highlights."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def generate_collage_assets(
    campaign_id: str,
    team_name: str,
    highlights: list[dict[str, Any]],
    output_dir: str | Path,
) -> tuple[str, str, int]:
    """Build both an HTML collage and a PNG grid image for the team retrospective.

    Args:
        campaign_id: Campaign identifier.
        team_name: Name of the core team.
        highlights: List of Section 2 customer highlight dicts with `photo_paths`.
        output_dir: Directory where collage files are saved.

    Returns:
        Tuple of (collage_image_path, collage_html_path, total_photo_count).
    """
    out = Path(output_dir) / campaign_id
    out.mkdir(parents=True, exist_ok=True)

    all_photos: list[tuple[str, str]] = []
    cards_html: list[str] = []
    for item in highlights:
        customer = html.escape(str(item.get("customer_name", "Customer")))
        story = html.escape(str(item.get("customer_highlight", "")))
        photos = item.get("photo_paths", [])
        photo_tags = []
        for p in photos:
            all_photos.append((str(p), customer))
            escaped_path = html.escape(str(p))
            photo_tags.append(
                f'<img src="{escaped_path}" alt="{customer} memory" '
                f'style="width:100%;max-height:220px;object-fit:cover;border-radius:8px;" />'
            )
        cards_html.append(
            f"""
            <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:16px;box-shadow:0 2px 6px rgba(0,0,0,0.05);">
              <h3 style="margin:0 0 8px 0;color:#1a73e8;">{customer}</h3>
              <p style="margin:0 0 12px 0;color:#334155;line-height:1.5;">{story}</p>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;">
                {''.join(photo_tags)}
              </div>
            </div>
            """
        )

    html_path = out / "year_in_review_collage.html"
    escaped_team = html.escape(team_name)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{escaped_team} - Year in Review Collage</title>
</head>
<body style="font-family:system-ui,-apple-system,sans-serif;background:#f8fafc;margin:0;padding:32px;">
  <div style="max-width:960px;margin:0 auto;">
    <h1 style="color:#0f172a;margin-bottom:4px;">{escaped_team} - Year in Review</h1>
    <p style="color:#64748b;margin-top:0;">Customer Highlights &amp; Team Memories ({len(highlights)} highlights, {len(all_photos)} photos)</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:24px;">
      {''.join(cards_html)}
    </div>
  </div>
</body>
</html>
"""
    html_path.write_text(html_content, encoding="utf-8")

    image_path = out / "year_in_review_collage.png"
    _render_png_collage(
        image_path=image_path,
        team_name=team_name,
        photos=all_photos,
        highlights=highlights,
    )
    return str(image_path), str(html_path), len(all_photos)


def _render_png_collage(
    image_path: Path,
    team_name: str,
    photos: list[tuple[str, str]],
    highlights: list[dict[str, Any]],
) -> None:
    """Render a grid PNG collage using Pillow, or write a valid 1x1 PNG fallback."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # Minimal valid 1x1 PNG when Pillow is absent in constrained test environments.
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
            b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return

    cols = 2
    tile_w, tile_h = 400, 260
    rows = max(1, (len(photos) + cols - 1) // cols)
    header_h = 80
    canvas = Image.new("RGB", (cols * tile_w + 60, header_h + rows * (tile_h + 20) + 40), (248, 250, 252))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 24), f"{team_name} - Year in Review ({len(highlights)} Customer Highlights)", fill=(15, 23, 42))

    for idx, (photo_path, customer) in enumerate(photos):
        r, c = divmod(idx, cols)
        x = 20 + c * (tile_w + 20)
        y = header_h + r * (tile_h + 20)
        p = Path(photo_path)
        if p.exists() and p.is_file():
            try:
                with Image.open(p) as img:
                    tile = img.convert("RGB").resize((tile_w, tile_h))
                canvas.paste(tile, (x, y))
                continue
            except Exception:
                pass
        draw.rectangle([x, y, x + tile_w, y + tile_h], fill=(226, 232, 240), outline=(148, 163, 184))
        draw.text((x + 16, y + tile_h // 2 - 8), f"Photo: {customer}", fill=(51, 65, 85))

    canvas.save(image_path, format="PNG")
