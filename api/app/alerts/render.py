"""What alert and digest emails say and look like: an HTML part (inline styles, the approved
light palette, a mini chart as an inline image) and a plain-text part with the same facts.

Every email links to the stock page and the alerts centre; it never asks for anything to be
bought or sold, it describes what happened and what the plan says.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from io import BytesIO
from typing import Any

from matplotlib.figure import Figure

from app.alerts.email import InlineImage, Message

# The light theme's tokens (web/app/globals.css): emails are read on light backgrounds.
INK = "#1b2029"
MUTED = "#5d636c"
BORDER = "#dedad0"
SURFACE = "#ffffff"
PAGE = "#f7f5f0"
RISE = "#2468c8"
FALL = "#c4531a"
TIDE = "#008c7c"
TIDE_INK = "#00756a"
GRID = "#ebe7de"
CHART_CID = "chart"
FONT = "'IBM Plex Sans', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


@dataclass(frozen=True)
class ChartBar:
    date: date
    high: float
    low: float
    close: float


def _price(value: Any) -> str | None:
    return None if value is None else f"{float(value):,.2f}"


def mini_chart(
    bars: Sequence[ChartBar],
    *,
    last: float | None = None,
    pivot: float | None = None,
    buy_zone_top: float | None = None,
    stop: float | None = None,
) -> bytes | None:
    """A 560×220 PNG: closes for the last sessions, today's price as a dot, the pivot (teal,
    with the buy zone shaded) and the stop (orange), each labelled at the right."""
    if len(bars) < 2:
        return None
    fig = Figure(figsize=(5.6, 2.2), dpi=100, facecolor=SURFACE)
    ax = fig.add_axes((0.02, 0.1, 0.84, 0.86))
    ax.set_facecolor(SURFACE)
    xs = list(range(len(bars)))
    closes = [b.close for b in bars]
    ax.vlines(xs, [b.low for b in bars], [b.high for b in bars], color="#c9c5bb", linewidth=1)
    ax.plot(xs, closes, color=INK, linewidth=1.6)
    end = len(bars) - 1
    if last is not None:
        end += 1
        ax.plot([xs[-1], end], [closes[-1], last], color=INK, linewidth=1.6, linestyle=":")
        ax.plot([end], [last], marker="o", markersize=6, color=INK, markeredgecolor=SURFACE)
    lows = [b.low for b in bars] + [v for v in (last, pivot, stop, buy_zone_top) if v is not None]
    highs = [b.high for b in bars] + [v for v in (last, pivot, stop) if v is not None]
    pad = (max(highs) - min(lows)) * 0.06 or 1
    ax.set_ylim(min(lows) - pad, max(highs) + pad)
    ax.set_xlim(-0.5, end + 0.5)
    if pivot is not None and buy_zone_top is not None:
        ax.axhspan(pivot, buy_zone_top, color=TIDE, alpha=0.12, linewidth=0)
    for level, colour, label in (
        (pivot, TIDE_INK, "Pivot"),
        (stop, FALL, "Stop"),
    ):
        if level is None:
            continue
        ax.axhline(level, color=colour, linewidth=1.4, linestyle="--")
        ax.annotate(
            f"{label} {level:,.2f}",
            (1.0, level),
            xycoords=("axes fraction", "data"),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=colour,
            annotation_clip=False,
        )
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=7, colors=MUTED, length=0, labelleft=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.annotate(
        f"{bars[0].date:%b %-d} to {bars[-1].date:%b %-d}",
        (0, 0),
        xycoords="axes fraction",
        xytext=(0, -10),
        textcoords="offset points",
        fontsize=7,
        color=MUTED,
    )
    out = BytesIO()
    fig.savefig(out, format="png", facecolor=SURFACE)
    return out.getvalue()


def facts(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """The numbers worth showing for an alert, as (label, value) rows."""
    rows: list[tuple[str, str]] = []
    if (price := _price(payload.get("price"))) is not None:
        change = payload.get("change_pct")
        rows.append(("Price", price + (f" ({change:+.1f}%)" if change is not None else "")))
    if (ratio := payload.get("volume_ratio_pct")) is not None:
        note = " (IEX, scaled)" if payload.get("partial_volume") else ""
        rows.append(("Projected volume", f"{ratio:.0f}% of 50-day average{note}"))
    if (pivot := _price(payload.get("pivot"))) is not None:
        top = _price(payload.get("buy_zone_top"))
        rows.append(("Pivot", pivot + (f" (buy zone to {top})" if top else "")))
    entry, stop = _price(payload.get("entry")), _price(payload.get("stop"))
    if entry and stop:
        shares = payload.get("shares")
        rows.append(
            ("Plan", f"entry {entry}, stop {stop}" + (f", {shares:,} shares" if shares else ""))
        )
    if payload.get("grade"):
        score = payload.get("score")
        rows.append(("Setup", f"{payload['grade']}" + (f" · {score:.0f}/100" if score else "")))
    if (r := payload.get("r")) is not None:
        rows.append(("Open P&L", f"{r:+.1f}R"))
    return rows


def _button(href: str, label: str, primary: bool) -> str:
    style = (
        f"background:{RISE};color:#ffffff;"
        if primary
        else f"color:{RISE};border:1px solid {BORDER};"
    )
    return (
        f'<a href="{escape(href)}" style="{style}display:inline-block;padding:9px 14px;'
        f'border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;margin-right:8px">'
        f"{escape(label)}</a>"
    )


def _frame(title: str, inner: str, footer: str) -> str:
    return (
        f'<!doctype html><html><body style="margin:0;background:{PAGE};font-family:{FONT};'
        f'color:{INK}"><div style="max-width:600px;margin:0 auto;padding:20px 16px">'
        f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;'
        f'color:{MUTED};margin-bottom:8px">Breakout</div>'
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'padding:20px"><h1 style="font-size:20px;line-height:1.3;margin:0 0 10px">'
        f"{escape(title)}</h1>{inner}</div>"
        f'<p style="font-size:12px;color:{MUTED};line-height:1.5;margin:14px 2px">{footer}</p>'
        "</div></body></html>"
    )


PRIORITY_MARK = {"high": "▲ High priority", "normal": "Normal priority"}


def alert_message(
    alert: dict[str, Any], to: str, public_url: str, chart: bytes | None = None
) -> Message:
    """One alert as an email. `alert` is app.alerts.engine.alert_json's shape."""
    base = public_url.rstrip("/")
    symbol = alert.get("symbol")
    stock_url = f"{base}/stocks/{symbol}" if symbol else None
    alerts_url = f"{base}/alerts"
    rows = facts(alert.get("payload") or {})
    table = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:{MUTED};white-space:nowrap">'
        f"{escape(label)}</td>"
        f'<td style="padding:4px 0;font-variant-numeric:tabular-nums">{escape(value)}</td></tr>'
        for label, value in rows
    )
    priority = PRIORITY_MARK.get(alert["priority"], alert["priority"])
    inner = (
        f'<p style="margin:0 0 4px;font-size:12px;color:{MUTED}">{escape(priority)} · '
        f"{escape(alert['session_date'])}</p>"
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.5">{escape(alert["body"])}</p>'
        + (f'<table style="font-size:14px;margin:0 0 14px">{table}</table>' if table else "")
        + (
            f'<img src="cid:{CHART_CID}" width="560" alt="{escape(symbol or "")} daily closes '
            f'with the pivot and stop" style="max-width:100%;height:auto;display:block;'
            f'margin:0 0 14px;border:1px solid {BORDER};border-radius:6px">'
            if chart
            else ""
        )
        + (_button(stock_url, f"Open {symbol}", True) if stock_url else "")
        + _button(alerts_url, "All alerts", stock_url is None)
    )
    footer = (
        f"Breakout screens, scores and alerts; it never places trades. Change what you get in "
        f'<a href="{escape(alerts_url)}" style="color:{MUTED}">alert settings</a>.'
    )
    text_rows = "\n".join(f"{label}: {value}" for label, value in rows)
    text = "\n\n".join(
        part
        for part in (
            alert["title"],
            f"{priority} · {alert['session_date']}",
            alert["body"],
            text_rows,
            f"Open {symbol}: {stock_url}" if stock_url else "",
            f"All alerts: {alerts_url}",
            "Breakout screens, scores and alerts; it never places trades.",
        )
        if part
    )
    images = (InlineImage(CHART_CID, f"{symbol or 'chart'}.png", chart),) if chart else ()
    return Message(to, alert["title"], _frame(alert["title"], inner, footer), text, images)


@dataclass(frozen=True)
class DigestSetup:
    symbol: str
    grade: str | None
    score: float
    state: str
    pattern: str | None
    pivot: float | None
    readiness_pct: float | None


@dataclass(frozen=True)
class Digest:
    kind: str  # daily | weekly
    title: str
    period: str  # "Friday, October 2" / "Week of September 28"
    regime: str | None
    alerts: list[dict[str, Any]]  # not emailed yet (digest / held / failed)
    emailed: int  # alerts already sent one by one
    setups: list[DigestSetup]
    lines: list[str]  # extra summary lines (weekly: signals and outcomes, holdings)


def digest_message(digest: Digest, to: str, public_url: str) -> Message:
    base = public_url.rstrip("/")
    blocks: list[str] = []
    text: list[str] = [digest.title, digest.period]
    if digest.regime:
        blocks.append(f'<p style="margin:0 0 12px;font-size:14px">{escape(digest.regime)}</p>')
        text.append(digest.regime)
    for line in digest.lines:
        blocks.append(f'<p style="margin:0 0 8px;font-size:14px">{escape(line)}</p>')
        text.append(line)

    heading = '<h2 style="font-size:15px;margin:18px 0 8px">{}</h2>'
    if digest.alerts:
        blocks.append(heading.format(f"Alerts ({len(digest.alerts)})"))
        text.append(f"Alerts ({len(digest.alerts)})")
        items = []
        for a in digest.alerts:
            link = f"{base}/stocks/{a['symbol']}" if a.get("symbol") else f"{base}/alerts"
            mark = "▲ " if a["priority"] == "high" else ""
            items.append(
                f'<li style="margin:0 0 8px"><a href="{escape(link)}" style="color:{RISE};'
                f'font-weight:600;text-decoration:none">{escape(mark + a["title"])}</a>'
                f'<div style="color:{MUTED};font-size:13px">{escape(a["body"])}</div></li>'
            )
            text.append(f"- {mark}{a['title']}: {a['body']}")
        blocks.append(
            f'<ul style="padding-left:18px;margin:0;font-size:14px">{"".join(items)}</ul>'
        )
    if digest.emailed:
        plural = "s" if digest.emailed != 1 else ""
        note = f"{digest.emailed} more alert{plural} went out by email as they happened."
        blocks.append(f'<p style="margin:8px 0 0;font-size:13px;color:{MUTED}">{escape(note)}</p>')
        text.append(note)
    if not digest.alerts and not digest.emailed:
        blocks.append(f'<p style="margin:0;font-size:14px;color:{MUTED}">No alerts.</p>')
        text.append("No alerts.")

    if digest.setups:
        blocks.append(heading.format("Top setups"))
        text.append("Top setups")
        rows = []
        for s in digest.setups:
            where = (
                ""
                if s.readiness_pct is None
                else f"{s.readiness_pct:.1f}% below pivot"
                if s.readiness_pct >= 0
                else f"{-s.readiness_pct:.1f}% above pivot"
            )
            cells = (
                f'<a href="{escape(base)}/stocks/{escape(s.symbol)}" style="color:{RISE};'
                f'font-weight:600;text-decoration:none">{escape(s.symbol)}</a>',
                escape(f"{s.grade or '—'} · {s.score:.0f}"),
                escape(s.pattern or s.state),
                escape(_price(s.pivot) or "—"),
                escape(where),
            )
            rows.append(
                "<tr>"
                + "".join(
                    f'<td style="padding:4px 10px 4px 0;font-variant-numeric:tabular-nums">{c}</td>'
                    for c in cells
                )
                + "</tr>"
            )
            text.append(
                f"- {s.symbol} {s.grade or '—'} {s.score:.0f} · {s.pattern or s.state} · pivot "
                f"{_price(s.pivot) or '—'} {where}".rstrip()
            )
        head = "".join(
            f'<th style="text-align:left;padding:4px 10px 4px 0;color:{MUTED};font-weight:500">'
            f"{h}</th>"
            for h in ("Stock", "Grade", "Pattern", "Pivot", "")
        )
        blocks.append(
            f'<table style="font-size:14px;border-collapse:collapse"><tr>{head}</tr>'
            f"{''.join(rows)}</table>"
        )
    blocks.append(f'<div style="margin-top:18px">{_button(base, "Open the dashboard", True)}</div>')
    text.append(f"Dashboard: {base}")
    alerts_url = f"{base}/alerts"
    footer = (
        f"Breakout screens, scores and alerts; it never places trades. Turn digests off in "
        f'<a href="{escape(alerts_url)}" style="color:{MUTED}">alert settings</a>.'
    )
    title = f"{digest.title} · {digest.period}"
    inner = f'<p style="margin:0 0 12px;font-size:12px;color:{MUTED}">{escape(digest.period)}</p>'
    return Message(
        to, title, _frame(digest.title, inner + "".join(blocks), footer), "\n\n".join(text)
    )
