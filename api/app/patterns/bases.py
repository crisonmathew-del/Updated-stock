"""Base detectors (spec §6.7): VCP, flat base, cup with handle, ascending base, high tight flag.

Each looks at the bars up to the as-of session and reports a base that is still forming or
broke out in the last few sessions, with its pivot, depth, swing points and a quality score
made of named components. When several candidates fit, the best-scoring one is reported.
Rules (hard requirements) and the score (how textbook the base is) are documented per detector.
"""

from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from app.patterns.bars import Bars, WeeklyBars
from app.patterns.context import (
    Breakout,
    add_base_count_score,
    add_position_score,
    add_rs_score,
    add_tightness_score,
    add_volume_score,
    base_number,
    breakout_status,
    is_left_side_high,
    linear,
    pct,
    qualifying_uptrend,
)
from app.patterns.swings import HIGH, LOW, Swing
from app.patterns.types import Contraction, PatternMatch, PatternType, Point, Scorecard
from app.settings.schema import AppSettings

WEEK = 5  # sessions


def _point(bars: Bars, index: int, kind: str) -> Point:
    price = bars.high[index] if kind == HIGH else bars.low[index]
    return Point(bars.dates[index], float(price), kind)


def _common_scores(
    card: Scorecard,
    bars: Bars,
    settings: AppSettings,
    *,
    start: int,
    end: int,
    base_low: float,
    pivot: float,
    number: int | None,
    weights: dict[str, float],
) -> None:
    if "volume" in weights:
        add_volume_score(card, bars, end, settings, weights["volume"])
    if "tightness" in weights:
        add_tightness_score(card, bars, end, weights["tightness"])
    if "position" in weights:
        add_position_score(card, bars, end, base_low, pivot, weights["position"])
    if "base_count" in weights:
        add_base_count_score(card, number, settings, weights["base_count"])
    if "rs_line" in weights:
        add_rs_score(card, bars, start, end, weights["rs_line"])


def _best(matches: Sequence[PatternMatch]) -> PatternMatch | None:
    return max(matches, key=lambda m: m.quality, default=None)


# --- VCP ------------------------------------------------------------------------------------


def detect_vcp(bars: Bars, settings: AppSettings, swings: Sequence[Swing]) -> PatternMatch | None:
    """Volatility contraction pattern. Starting from a swing high H0 (the base's high), the
    swings alternate high/low; each (high, following low) pair is a contraction.

    Rules: vcp_min..vcp_max contractions; each no deeper than vcp_contraction_ratio_max × the
    previous; the first no deeper than vcp_first_contraction_max_pct, the last no deeper than
    vcp_final_contraction_max_pct; no high above H0; the last low at or above the first low
    (the right side holds); 3-65 weeks from H0; a prior uptrend into H0. Pivot = the high of
    the final contraction. The final low may still be forming (provisional). H0 must be the
    highest high of the base_left_side_days before it; the earliest qualifying H0 wins (the
    whole base, not a later part of it)."""
    s = settings
    t = bars.last
    for k, h0 in enumerate(swings):
        if h0.kind != HIGH or not s.vcp_min_weeks * WEEK <= t - h0.index <= s.vcp_max_weeks * WEEK:
            continue
        if not is_left_side_high(bars, h0.index, s):
            continue
        sequence = list(swings[k:])
        pairs = [(sequence[i], sequence[i + 1]) for i in range(0, len(sequence) - 1, 2)]
        if not s.vcp_min_contractions <= len(pairs) <= s.vcp_max_contractions:
            continue
        if any(high.price > h0.price for high, _ in pairs[1:]):
            continue
        depths = [pct(low.price, high.price) for high, low in pairs]
        if (
            depths[0] > s.vcp_first_contraction_max_pct
            or depths[-1] > s.vcp_final_contraction_max_pct
        ):
            continue
        if any(b > s.vcp_contraction_ratio_max * a for a, b in pairwise(depths)):
            continue
        final_high, final_low = pairs[-1]
        if final_low.price < pairs[0][1].price:
            continue
        gain = qualifying_uptrend(bars, h0.index, s)
        if gain is None:
            continue
        pivot = final_high.price
        status = breakout_status(bars, pivot, final_low.index)
        if status is None:
            continue
        return _vcp_match(bars, s, h0, pairs, depths, pivot, status, gain)
    return None


def _vcp_match(
    bars: Bars,
    s: AppSettings,
    h0: Swing,
    pairs: list[tuple[Swing, Swing]],
    depths: list[float],
    pivot: float,
    status: Breakout,
    gain: float,
) -> PatternMatch:
    end = status.end
    base_low = min(low.price for _, low in pairs)
    number = base_number(bars, h0.index, base_low, s)
    card = Scorecard()
    final = depths[-1]
    trail = " → ".join(f"{d:.1f}%" for d in depths)
    card.add(
        "depth",
        "Depth of the contractions",
        15,
        1.0 if final <= 6 else linear(final, 6, s.vcp_final_contraction_max_pct + 4),
        f"Contractions {trail}; the final one {final:.1f}% (ideal under 6%, limit "
        f"{s.vcp_final_contraction_max_pct:g}%).",
    )
    ratios = [b / a for a, b in pairwise(depths) if a > 0]
    mean_ratio = sum(ratios) / len(ratios) if ratios else 1.0
    count_fit = {2: 0.6, 3: 1.0, 4: 1.0}.get(len(pairs), 0.8)
    card.add(
        "progression",
        "Contraction progression",
        20,
        count_fit * (0.6 + 0.4 * linear(mean_ratio, 0.5, s.vcp_contraction_ratio_max)),
        f"{len(pairs)} contractions, each on average {mean_ratio:.2f}× the previous "
        f"(limit {s.vcp_contraction_ratio_max:g}×; 3-4 contractions are ideal).",
    )
    volumes = [float(bars.volume[h.index : lo.index + 1].mean()) for h, lo in pairs]
    trend = volumes[-1] / volumes[0] if volumes[0] > 0 else 1.0
    card.add(
        "volume_trend",
        "Volume contracting with price",
        8,
        linear(trend, 0.6, 1.0),
        f"Average volume in the final contraction is {trend * 100:.0f}% of the first's.",
    )
    _common_scores(
        card,
        bars,
        s,
        start=h0.index,
        end=end,
        base_low=base_low,
        pivot=pivot,
        number=number,
        weights={"volume": 12, "tightness": 15, "position": 10, "base_count": 10, "rs_line": 10},
    )
    points = [p for pair in pairs for p in pair]
    return PatternMatch(
        type=PatternType.VCP,
        timeframe="daily",
        start=bars.dates[h0.index],
        end=bars.dates[end],
        pivot=pivot,
        status=status.status,
        duration_weeks=(end - h0.index) / WEEK,
        components=card.components,
        base_low=base_low,
        depth_pct=depths[0],
        base_number=number,
        swings=[_point(bars, p.index, p.kind) for p in points],
        contractions=[
            Contraction(_point(bars, h.index, HIGH), _point(bars, lo.index, LOW), d)
            for (h, lo), d in zip(pairs, depths, strict=True)
        ],
        details={
            "prior_uptrend_pct": round(gain, 1),
            "final_low_provisional": pairs[-1][1].confirmed_at < 0,
            "contraction_volume": [round(v) for v in volumes],
            "breakout_date": None if status.index is None else bars.dates[status.index].isoformat(),
        },
    )


# --- Flat base ------------------------------------------------------------------------------


def detect_flat_base(
    bars: Bars, settings: AppSettings, swings: Sequence[Swing]
) -> PatternMatch | None:
    """Flat base: a sideways range at least flat_base_min_weeks long and no deeper than
    flat_base_max_depth_pct, starting at a swing high that price hasn't exceeded since (and the
    highest of the base_left_side_days before it), after a prior uptrend. Pivot = the base
    high. The longest qualifying base is reported."""
    s = settings
    t = bars.last
    for h0 in swings:
        if h0.kind != HIGH or h0.confirmed_at < 0 or not is_left_side_high(bars, h0.index, s):
            continue
        if not s.flat_base_min_weeks * WEEK <= t - h0.index <= s.cup_max_weeks * WEEK:
            continue
        pivot = h0.price
        status = breakout_status(bars, pivot, h0.index)
        if status is None:
            continue
        end = status.end
        if (
            end - h0.index < s.flat_base_min_weeks * WEEK
            or bars.high[h0.index + 1 : end + 1].max() > pivot
        ):
            continue
        base_low = float(bars.low[h0.index : end + 1].min())
        depth = pct(base_low, pivot)
        if depth > s.flat_base_max_depth_pct:
            continue
        gain = qualifying_uptrend(bars, h0.index, s)
        if gain is None:
            continue
        weeks = (end - h0.index) / WEEK
        number = base_number(bars, h0.index, base_low, s)
        card = Scorecard()
        card.add(
            "depth",
            "Depth",
            20,
            1.0 if depth <= 10 else linear(depth, 10, s.flat_base_max_depth_pct + 5),
            f"{depth:.1f}% from high to low (limit {s.flat_base_max_depth_pct:g}%; "
            "10% or less is ideal).",
        )
        card.add(
            "duration",
            "Duration",
            10,
            1.0 if weeks <= 12 else 0.7,
            f"{weeks:.1f} weeks (at least {s.flat_base_min_weeks:g}).",
        )
        _common_scores(
            card,
            bars,
            s,
            start=h0.index,
            end=end,
            base_low=base_low,
            pivot=pivot,
            number=number,
            weights={
                "volume": 20,
                "tightness": 20,
                "position": 10,
                "base_count": 10,
                "rs_line": 10,
            },
        )
        low_index = h0.index + int(np.argmin(bars.low[h0.index : end + 1]))
        return PatternMatch(
            type=PatternType.FLAT_BASE,
            timeframe="daily",
            start=bars.dates[h0.index],
            end=bars.dates[end],
            pivot=pivot,
            status=status.status,
            duration_weeks=weeks,
            components=card.components,
            base_low=base_low,
            depth_pct=depth,
            base_number=number,
            swings=[_point(bars, h0.index, HIGH), _point(bars, low_index, LOW)],
            details={
                "prior_uptrend_pct": round(gain, 1),
                "breakout_date": None
                if status.index is None
                else bars.dates[status.index].isoformat(),
            },
        )
    return None


# --- Cup with handle ------------------------------------------------------------------------


def detect_cup_with_handle(
    bars: Bars,
    settings: AppSettings,
    daily: Sequence[Swing],
    weeks: WeeklyBars,
    weekly_swings: Sequence[Swing],
) -> PatternMatch | None:
    """Cup with handle. The left lip is a weekly swing high (the day of that week's high); the
    cup bottom is the lowest low between the lip and the right-side peak; the right-side peak
    is a confirmed daily swing high whose pullback is the handle.

    Rules: cup depth cup_min_depth_pct..cup_max_depth_pct (cup_bear_market_max_depth_pct if the
    market was in correction during the cup); U-shaped, not V: at least
    cup_min_bottom_share_pct of the cup's closes in its bottom third; the right side recovers
    into the upper half without exceeding the lip; the handle is handle_min..handle_max_depth_pct
    deep, at least handle_min_days long, entirely in the upper half of the cup and below its own
    high; cup_min..cup_max_weeks in all; a prior uptrend into the lip. Pivot = the handle high.
    The bear-market depth applies only if the market was in correction between the lip and the
    cup's low (the decline happened in a bear market)."""
    s = settings
    t = bars.last
    lips = []
    for w in weekly_swings:
        if w.kind != HIGH or w.confirmed_at < 0:
            continue
        a0, a1 = int(weeks.first[w.index]), int(weeks.last[w.index])
        lips.append(a0 + int(np.argmax(bars.high[a0 : a1 + 1])))
    right_peaks = [sw.index for sw in daily if sw.kind == HIGH and sw.confirmed_at >= 0]
    found = []
    for a in lips:
        if not s.cup_min_weeks * WEEK <= t - a <= s.cup_max_weeks * WEEK:
            continue
        lip = float(bars.high[a])
        gain = qualifying_uptrend(bars, a, s)
        if gain is None:
            continue
        # A right-side peak at or after the first high above the lip can never qualify (the
        # cup may not exceed its lip): skip those without building the candidate.
        higher = np.flatnonzero(bars.high[a + 1 : t + 1] > lip)
        limit = a + 1 + int(higher[0]) if len(higher) else t + 1
        for r in reversed(right_peaks):
            if r <= a + 2:
                break
            if r >= limit:
                continue
            match = _cup_candidate(bars, s, a, r, lip, gain)
            if match is not None:
                found.append(match)
                break
    return _best(found)


def _cup_candidate(
    bars: Bars, s: AppSettings, a: int, r: int, lip: float, gain: float
) -> PatternMatch | None:
    b = a + int(np.argmin(bars.low[a : r + 1]))
    if not a < b < r:
        return None
    bottom = float(bars.low[b])
    depth = pct(bottom, lip)
    # The deeper limit applies when the market was in correction while the cup was falling.
    bear = bool(bars.market_correction[a : b + 1].any())
    max_depth = s.cup_bear_market_max_depth_pct if bear else s.cup_max_depth_pct
    if not s.cup_min_depth_pct <= depth <= max_depth:
        return None
    right = float(bars.high[r])
    middle = bottom + (lip - bottom) / 2
    if right > lip or bars.high[a + 1 : r].max() > lip or right < middle:
        return None
    status = breakout_status(bars, right, r)
    if status is None:
        return None
    end = status.end
    if end - r < s.handle_min_days or bars.high[r + 1 : end + 1].max() > right:
        return None
    handle_low_index = r + 1 + int(np.argmin(bars.low[r + 1 : end + 1]))
    handle_low = float(bars.low[handle_low_index])
    handle_depth = pct(handle_low, right)
    if not s.handle_min_depth_pct <= handle_depth <= s.handle_max_depth_pct or handle_low < middle:
        return None
    bottom_third = bottom + (lip - bottom) / 3
    share = float(np.mean(bars.close[a : r + 1] <= bottom_third)) * 100
    if share < s.cup_min_bottom_share_pct:
        return None
    weeks = (end - a) / WEEK
    if not s.cup_min_weeks <= weeks <= s.cup_max_weeks:
        return None

    number = base_number(bars, a, bottom, s)
    card = Scorecard()
    ideal = 15 <= depth <= 30
    card.add(
        "depth",
        "Cup depth",
        15,
        1.0 if ideal else 0.7 if depth <= s.cup_max_depth_pct else 0.5,
        f"Cup {depth:.1f}% deep (allowed {s.cup_min_depth_pct:g}-{max_depth:g}%"
        f"{', deeper allowed: market in correction' if bear else ''}; 15-30% is ideal).",
    )
    card.add(
        "shape",
        "U-shaped bottom",
        15,
        0.5 + 0.5 * linear(share, 30, s.cup_min_bottom_share_pct),
        f"{share:.0f}% of the cup's closes are in its bottom third (needs "
        f"{s.cup_min_bottom_share_pct:g}%; a V-shaped cup spends little time there).",
    )
    handle_volume = float(bars.volume[r + 1 : end + 1].mean())
    average = bars.avg_volume_50[r]
    light = bool(np.isfinite(average) and handle_volume < average)
    closes = bars.close[r + 1 : end + 1]
    drifting = len(closes) < 2 or closes[-1] <= closes[0] * 1.02
    upper_third = handle_low >= bottom + (lip - bottom) * 2 / 3
    card.add(
        "handle",
        "Handle",
        15,
        0.5 * light + 0.25 * drifting + 0.25 * upper_third,
        f"Handle {handle_depth:.1f}% deep over {end - r} sessions; volume "
        f"{'below' if light else 'not below'} average; "
        f"{'drifting down or sideways' if drifting else 'rising'}; "
        f"{'in the upper third' if upper_third else 'in the upper half'} of the cup.",
    )
    _common_scores(
        card,
        bars,
        s,
        start=a,
        end=end,
        base_low=bottom,
        pivot=right,
        number=number,
        weights={"volume": 15, "tightness": 10, "position": 10, "base_count": 10, "rs_line": 10},
    )
    return PatternMatch(
        type=PatternType.CUP_WITH_HANDLE,
        timeframe="weekly",
        start=bars.dates[a],
        end=bars.dates[end],
        pivot=right,
        status=status.status,
        duration_weeks=weeks,
        components=card.components,
        base_low=bottom,
        depth_pct=depth,
        base_number=number,
        swings=[
            _point(bars, a, HIGH),
            _point(bars, b, LOW),
            _point(bars, r, HIGH),
            _point(bars, handle_low_index, LOW),
        ],
        details={
            "prior_uptrend_pct": round(gain, 1),
            "handle_depth_pct": round(handle_depth, 2),
            "handle_sessions": end - r,
            "bottom_third_share_pct": round(share, 1),
            "bear_market_depth": bear,
            "breakout_date": None if status.index is None else bars.dates[status.index].isoformat(),
        },
    )


# --- Ascending base -------------------------------------------------------------------------


def detect_ascending_base(
    bars: Bars, settings: AppSettings, swings: Sequence[Swing]
) -> PatternMatch | None:
    """Ascending base: three pullbacks of ascending_pullback_min..max_pct each, every high and
    every low above the previous one, over ascending_min..max_weeks. `swings` come from a
    ZigZag with an ascending_pullback_min_pct threshold, so each swing low is a qualifying
    pullback. Pivot = the high before the third pullback."""
    s = settings
    t = bars.last
    points = list(swings)
    if points and points[-1].kind == HIGH:
        points = points[:-1]  # the rally after the third pullback
    if len(points) < 6:
        return None
    sequence = points[-6:]
    if [p.kind for p in sequence] != [HIGH, LOW] * 3:
        return None
    pairs = [(sequence[i], sequence[i + 1]) for i in (0, 2, 4)]
    highs = [h.price for h, _ in pairs]
    lows = [lo.price for _, lo in pairs]
    if not (highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2]):
        return None
    depths = [pct(lo.price, h.price) for h, lo in pairs]
    if not all(s.ascending_pullback_min_pct <= d <= s.ascending_pullback_max_pct for d in depths):
        return None
    start = pairs[0][0].index
    if not s.ascending_min_weeks * WEEK <= t - start <= s.ascending_max_weeks * WEEK:
        return None
    gain = qualifying_uptrend(bars, start, s)
    if gain is None:
        return None
    pivot = highs[2]
    status = breakout_status(bars, pivot, pairs[2][1].index)
    if status is None:
        return None
    end = status.end
    number = base_number(bars, start, lows[0], s)
    card = Scorecard()
    trail = ", ".join(f"{d:.1f}%" for d in depths)
    card.add(
        "pullbacks",
        "Three ascending pullbacks",
        30,
        0.7 + 0.3 * (depths[2] <= depths[0]),
        f"Pullbacks of {trail}, each high and low above the last "
        f"(each {s.ascending_pullback_min_pct:g}-{s.ascending_pullback_max_pct:g}%).",
    )
    _common_scores(
        card,
        bars,
        s,
        start=start,
        end=end,
        base_low=lows[0],
        pivot=pivot,
        number=number,
        weights={"volume": 20, "tightness": 10, "position": 10, "base_count": 10, "rs_line": 20},
    )
    return PatternMatch(
        type=PatternType.ASCENDING_BASE,
        timeframe="daily",
        start=bars.dates[start],
        end=bars.dates[end],
        pivot=pivot,
        status=status.status,
        duration_weeks=(end - start) / WEEK,
        components=card.components,
        base_low=lows[0],
        depth_pct=max(depths),
        base_number=number,
        swings=[_point(bars, p.index, p.kind) for p in sequence],
        contractions=[
            Contraction(_point(bars, h.index, HIGH), _point(bars, lo.index, LOW), d)
            for (h, lo), d in zip(pairs, depths, strict=True)
        ],
        details={
            "prior_uptrend_pct": round(gain, 1),
            "breakout_date": None if status.index is None else bars.dates[status.index].isoformat(),
        },
    )


# --- High tight flag ------------------------------------------------------------------------


def detect_high_tight_flag(
    bars: Bars, settings: AppSettings, swings: Sequence[Swing]
) -> PatternMatch | None:
    """High tight flag: a pole of at least htf_min_gain_pct within htf_max_pole_weeks, then a
    flag pulling back htf_flag_min..max_depth_pct over htf_flag_min..max_weeks on lighter
    volume than the pole, never above the pole's top. Pivot = the top of the pole."""
    s = settings
    t = bars.last
    pole_sessions = int(s.htf_max_pole_weeks * WEEK)
    found = []
    for top in swings:
        if top.kind != HIGH or top.confirmed_at < 0:
            continue
        p1 = top.index
        if t - p1 > s.htf_flag_max_weeks * WEEK + 3 or p1 < pole_sessions:
            continue
        pivot = top.price
        status = breakout_status(bars, pivot, p1)
        if status is None:
            continue
        end = status.end
        flag_sessions = end - p1
        if not s.htf_flag_min_weeks * WEEK <= flag_sessions <= s.htf_flag_max_weeks * WEEK:
            continue
        if bars.high[p1 + 1 : end + 1].max() > pivot:
            continue
        flag_low_index = p1 + 1 + int(np.argmin(bars.low[p1 + 1 : end + 1]))
        depth = pct(float(bars.low[flag_low_index]), pivot)
        if not s.htf_flag_min_depth_pct <= depth <= s.htf_flag_max_depth_pct:
            continue
        p0 = p1 - pole_sessions + int(np.argmin(bars.low[p1 - pole_sessions : p1 + 1]))
        gain = (pivot / bars.low[p0] - 1) * 100
        if gain < s.htf_min_gain_pct:
            continue
        pole_volume = float(bars.volume[p0 : p1 + 1].mean())
        flag_volume = float(bars.volume[p1 + 1 : end + 1].mean())
        if flag_volume >= pole_volume:
            continue
        card = Scorecard()
        card.add(
            "pole",
            "Pole",
            25,
            0.6 + 0.4 * linear(gain, 120, s.htf_min_gain_pct),
            f"+{gain:.0f}% in {(p1 - p0) / WEEK:.1f} weeks (needs {s.htf_min_gain_pct:g}% within "
            f"{s.htf_max_pole_weeks:g} weeks).",
        )
        card.add(
            "flag",
            "Flag",
            25,
            1.0 if depth <= 15 else linear(depth, 15, s.htf_flag_max_depth_pct + 5),
            f"Flag {depth:.1f}% deep over {flag_sessions / WEEK:.1f} weeks "
            f"(allowed {s.htf_flag_min_depth_pct:g}-{s.htf_flag_max_depth_pct:g}%).",
        )
        ratio = flag_volume / pole_volume
        card.add(
            "volume",
            "Dry volume in the flag",
            25,
            linear(ratio, 0.5, 1.0),
            f"Flag volume averages {ratio * 100:.0f}% of the pole's.",
        )
        add_tightness_score(card, bars, end, 10)
        add_rs_score(card, bars, p0, end, 15)
        found.append(
            PatternMatch(
                type=PatternType.HIGH_TIGHT_FLAG,
                timeframe="daily",
                start=bars.dates[p0],
                end=bars.dates[end],
                pivot=pivot,
                status=status.status,
                duration_weeks=(end - p0) / WEEK,
                components=card.components,
                base_low=float(bars.low[flag_low_index]),
                depth_pct=depth,
                swings=[
                    _point(bars, p0, LOW),
                    _point(bars, p1, HIGH),
                    _point(bars, flag_low_index, LOW),
                ],
                details={
                    "pole_gain_pct": round(gain, 1),
                    "pole_weeks": round((p1 - p0) / WEEK, 1),
                    "flag_volume_vs_pole": round(ratio, 2),
                    "breakout_date": None
                    if status.index is None
                    else bars.dates[status.index].isoformat(),
                },
            )
        )
    return _best(found)
