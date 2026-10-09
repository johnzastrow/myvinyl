"""Server-side SVG geometry for the value-history chart (no JavaScript needed)."""

from datetime import datetime

WIDTH, HEIGHT = 640, 180
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 64, 16, 14, 28


def _x_positions(times: list[datetime]) -> list[float]:
    span = (times[-1] - times[0]).total_seconds()
    inner = WIDTH - PAD_LEFT - PAD_RIGHT
    if span <= 0:
        # All points at the same moment: spread them evenly instead.
        step = inner / max(len(times) - 1, 1)
        return [PAD_LEFT + i * step for i in range(len(times))]
    return [PAD_LEFT + (t - times[0]).total_seconds() / span * inner for t in times]


def value_history(rows) -> dict | None:
    """Turn value_history rows into chart geometry.

    Returns None with fewer than two points. Otherwise a dict with the value line,
    the low-high band polygon, y-axis ticks, and first/last date labels.
    """
    points = [r for r in rows if r["value_cents"] is not None or r["low_cents"] is not None]
    if len(points) < 2:
        return None

    times = [datetime.strptime(r["checked_at"], "%Y-%m-%d %H:%M:%S") for r in points]
    values = [
        c
        for r in points
        for c in (r["value_cents"], r["low_cents"], r["high_cents"])
        if c is not None
    ]
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = max(lo - 100, 0), hi + 100  # keep a flat line off the edges
    inner_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    def y(cents: int) -> float:
        return PAD_TOP + (hi - cents) / (hi - lo) * inner_h

    xs = _x_positions(times)
    line = [
        (x, y(r["value_cents"]))
        for x, r in zip(xs, points, strict=True)
        if r["value_cents"] is not None
    ]
    band_rows = [
        (x, r)
        for x, r in zip(xs, points, strict=True)
        if r["low_cents"] is not None and r["high_cents"] is not None
    ]
    band = [(x, y(r["high_cents"])) for x, r in band_rows] + [
        (x, y(r["low_cents"])) for x, r in reversed(band_rows)
    ]

    def fmt(pairs):
        return " ".join(f"{px:.1f},{py:.1f}" for px, py in pairs)

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "left": PAD_LEFT,
        "right": WIDTH - PAD_RIGHT,
        "line": fmt(line),
        "dots": [{"x": round(px, 1), "y": round(py, 1)} for px, py in line],
        "band": fmt(band) if len(band_rows) >= 2 else "",
        "ticks": [{"y": round(y(c), 1), "cents": c} for c in (hi, (hi + lo) // 2, lo)],
        "first": points[0]["checked_at"],
        "last": points[-1]["checked_at"],
        "baseline": HEIGHT - PAD_BOTTOM,
    }
