"""Server-rendered PNG of a detection for the review page (an admin tool; the real chart
arrives with the Phase 5 design plan).

Price panel: hollow (up) / filled (down) candles in neutral ink, the 50/150/200-day SMAs in
the first three categorical slots (legend + direct labels at the right end), the base shaded,
pivot and base low as labelled lines, swing points as triangles (above highs, below lows) with
each contraction's depth, and a line at the detection date. Volume sits in its own panel with
its 50-day average (one y-axis per panel). Colours follow the validated reference palette.
"""

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

import numpy as np
import polars as pl
from matplotlib.figure import Figure  # the object API: no pyplot state, safe in threads


@dataclass(frozen=True)
class Theme:
    surface: str
    ink: str
    secondary: str
    muted: str
    grid: str
    axis: str
    series: tuple[str, str, str]
    marks: str
    shade: str


LIGHT = Theme(
    surface="#fcfcfb",
    ink="#0b0b0b",
    secondary="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a"),
    marks="#4a3aa7",
    shade="#f0efec",
)
DARK = Theme(
    surface="#1a1a19",
    ink="#ffffff",
    secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series=("#3987e5", "#d95926", "#199e70"),
    marks="#9085e9",
    shade="#383835",
)
AVERAGES = (("sma50", "50-day"), ("sma150", "150-day"), ("sma200", "200-day"))


def _shares(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:g}B"
    if value >= 1e6:
        return f"{value / 1e6:g}M"
    return f"{value / 1e3:g}K" if value else "0"


def _index(dates: list[date], day: date) -> int:
    """Position of `day` (or the last session before it) in `dates`."""
    return max(
        0,
        int(
            np.searchsorted(
                np.array(dates, dtype="datetime64[D]"), np.datetime64(day), side="right"
            )
        )
        - 1,
    )


def render_pattern_chart(
    frame: pl.DataFrame, pattern: dict[str, Any], *, title: str, dark: bool = False
) -> bytes:
    """`frame`: date, open, high, low, close, volume, sma50, sma150, sma200, avg_volume_50,
    sorted by date. `pattern`: a stored row (start_date, end_date, last_seen, pivot, base_low,
    swings, contractions)."""
    t = DARK if dark else LIGHT
    dates: list[date] = frame["date"].to_list()
    n = len(dates)
    x = np.arange(n)

    def col(name: str) -> np.ndarray:
        return frame[name].cast(pl.Float64).fill_null(np.nan).to_numpy()

    o, h, lo, c, v = (col(k) for k in ("open", "high", "low", "close", "volume"))

    fig = Figure(figsize=(10, 6), dpi=100, facecolor=t.surface)
    grid = fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.06)
    price = fig.add_subplot(grid[0])
    volume = fig.add_subplot(grid[1], sharex=price)
    for ax in (price, volume):
        ax.set_facecolor(t.surface)
        ax.grid(axis="y", color=t.grid, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(t.axis)
        ax.tick_params(colors=t.muted, labelsize=8, length=0)

    start = _index(dates, date.fromisoformat(str(pattern["start_date"])))
    end = _index(dates, date.fromisoformat(str(pattern["end_date"])))
    seen = _index(dates, date.fromisoformat(str(pattern["last_seen"])))

    # Base shading, pivot and base low.
    price.axvspan(start - 0.5, end + 0.5, color=t.shade, zorder=0)
    pivot = float(pattern["pivot"])
    right = n - 1
    price.hlines(pivot, start, right, colors=t.ink, linestyles="--", linewidth=1.0, zorder=3)
    # Right-edge labels (pivot, base low, averages) are placed together at the end, so they
    # never overprint each other.
    labels: list[tuple[float, str, str, int]] = [(pivot, f"Pivot {pivot:,.2f}", t.ink, 8)]
    if pattern.get("base_low") is not None:
        base_low = float(pattern["base_low"])
        price.hlines(base_low, start, right, colors=t.secondary, linestyles=":", linewidth=1.0)
        labels.append((base_low, f"Base low {base_low:,.2f}", t.secondary, 8))

    # Candles: wick lines, then bodies (hollow up, filled down).
    price.vlines(x, lo, h, colors=t.secondary, linewidth=0.7, zorder=2)
    up = c >= o
    body_low = np.minimum(o, c)
    body = np.maximum(np.abs(c - o), (np.nanmax(h) - np.nanmin(lo)) * 0.002)
    price.bar(
        x[up],
        body[up],
        bottom=body_low[up],
        width=0.6,
        color=t.surface,
        edgecolor=t.secondary,
        linewidth=0.7,
        zorder=2,
    )
    price.bar(
        x[~up],
        body[~up],
        bottom=body_low[~up],
        width=0.6,
        color=t.secondary,
        edgecolor=t.secondary,
        linewidth=0.7,
        zorder=2,
    )

    # Moving averages: legend plus a direct label at the right end.
    for (column, label), colour in zip(AVERAGES, t.series, strict=True):
        if column not in frame.columns:
            continue
        values = col(column)
        if np.all(np.isnan(values)):
            continue
        price.plot(x, values, color=colour, linewidth=1.5, label=label, zorder=4)
        last = int(np.flatnonzero(~np.isnan(values))[-1])
        labels.append((float(values[last]), label, t.secondary, 7))
    span = float(np.nanmax(h) - np.nanmin(lo)) or 1.0
    previous: float | None = None
    for value, text_label, colour, size in sorted(labels):
        y = value if previous is None else max(value, previous + 0.04 * span)
        previous = y
        price.annotate(
            text_label,
            (n - 1, y),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=size,
            color=colour,
        )

    # Swing points and contraction depths.
    for point in pattern.get("swings") or []:
        i = _index(dates, date.fromisoformat(point["date"]))
        is_high = point["kind"] == "high"
        price.plot(
            i,
            point["price"],
            marker="v" if is_high else "^",
            markersize=8,
            color=t.marks,
            markeredgecolor=t.surface,
            markeredgewidth=1.5,
            zorder=5,
            linestyle="none",
        )
    for contraction in pattern.get("contractions") or []:
        low = contraction["low"]
        i = _index(dates, date.fromisoformat(low["date"]))
        price.annotate(
            f"-{contraction['depth_pct']:.1f}%",
            (i, low["price"]),
            xytext=(0, -16),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=t.ink,
        )

    price.axvline(seen, color=t.muted, linewidth=0.8, linestyle="-.", zorder=1)
    price.annotate(
        "detected as of",
        (seen, 1),
        xycoords=("data", "axes fraction"),
        xytext=(-4, -2),
        textcoords="offset points",
        ha="right",
        va="top",
        fontsize=7,
        color=t.muted,
    )
    legend = price.legend(loc="upper left", fontsize=8, frameon=False)
    for text in legend.get_texts():
        text.set_color(t.secondary)
    price.set_title(title, loc="left", fontsize=11, color=t.ink, pad=10)
    price.tick_params(labelbottom=False)

    volume.bar(x, v, width=0.7, color=t.axis, zorder=2)
    if "avg_volume_50" in frame.columns:
        volume.plot(
            x,
            col("avg_volume_50"),
            color=t.secondary,
            linewidth=1.0,
            linestyle="--",
            label="50-day average volume",
        )
        vlegend = volume.legend(loc="upper left", fontsize=7, frameon=False)
        for text in vlegend.get_texts():
            text.set_color(t.secondary)
    volume.yaxis.set_major_formatter(lambda value, _: _shares(value))

    ticks = [i for i in range(1, n) if dates[i].month != dates[i - 1].month]
    if not ticks or ticks[0] > 10:
        ticks.insert(0, 0)  # label the first session unless a month starts right after it
    volume.set_xticks(ticks, [dates[i].strftime("%b %y") for i in ticks])
    price.set_xlim(-1, n + 6)
    top, bottom = float(np.nanmax(h)), float(np.nanmin(lo))
    span = top - bottom
    price.set_ylim(bottom - 0.08 * span, top + 0.05 * span)  # room for the depth labels

    buffer = BytesIO()
    fig.savefig(buffer, format="png", facecolor=t.surface, bbox_inches="tight")
    return buffer.getvalue()
