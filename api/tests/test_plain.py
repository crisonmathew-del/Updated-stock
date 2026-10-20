"""The plain-English summary: the setup's own facts in a few sentences, and the verdict."""

from dataclasses import replace

import pytest

from app.scoring.plain import PlainFacts, plain_summary
from app.scoring.verdict import Action, Check, Verdict

FACTS = PlainFacts(
    state="near_pivot",
    kind="base",
    rs_rating=91,
    group_name="Software",
    group_rank=12,
    groups_ranked=197,
    fundamentals_grade="B",
    pattern_label="Volatility contraction (VCP)",
    pivot=92.46,
    readiness_pct=1.04,
    volume_ratio=2.3,
)
READY = Verdict(
    Action.GET_READY,
    "Get ready",
    "It passes every rule: place the order tonight.",
    [Check("market", "Market", True, "Rising.", "")],
)


def test_a_base_near_its_pivot() -> None:
    assert plain_summary(FACTS, READY) == (
        "It has done better than 91% of all stocks over the past year, and its industry "
        "(Software) ranks 12 of 197. The company shows strong profit and sales growth "
        "(Fundamentals Grade B). "
        "It's resting in a volatility contraction (VCP), 1.0% below its buy point of 92.46. "
        "The app says: Get ready. It passes every rule: place the order tonight."
    )


@pytest.mark.parametrize(
    ("since", "sentence"),
    [
        (
            0,
            "It broke out of a volatility contraction (VCP) above its buy point of 92.46 today "
            "on 2.3× its usual volume.",
        ),
        # Later sessions' volume isn't the breakout's: the summary says when instead.
        (
            1,
            "It broke out of a volatility contraction (VCP) above its buy point of 92.46 1 "
            "session ago.",
        ),
        (
            3,
            "It broke out of a volatility contraction (VCP) above its buy point of 92.46 3 "
            "sessions ago.",
        ),
        (None, "It broke out of a volatility contraction (VCP) above its buy point of 92.46."),
        (10_000, "It broke out of a volatility contraction (VCP) above its buy point of 92.46."),
    ],
)
def test_a_breakout_quotes_volume_only_on_its_own_day(since: int | None, sentence: str) -> None:
    facts = replace(FACTS, state="breakout", readiness_pct=-1.5, sessions_since_breakout=since)
    assert sentence in plain_summary(facts, READY)


@pytest.mark.parametrize(
    ("changes", "sentence"),
    [
        (
            {"readiness_pct": -0.8},
            "It's just above its buy point of 92.46 in a volatility "
            "contraction (VCP), but without the heavy buying that confirms a breakout.",
        ),
        (
            {"state": "extended", "readiness_pct": -7.25},
            "It has already run 7.2% past its buy point of 92.46.",
        ),
        ({"state": "failed"}, "Its last buying pattern didn't work out."),
        (
            {"kind": "watch", "pivot": None},
            "It's in an uptrend but hasn't formed a buying pattern (a base) yet.",
        ),
        (
            {"fundamentals_grade": None},
            "There are no SEC financial statements to grade the business.",
        ),
        (
            {"rs_rating": 35, "group_name": None},
            "It has lagged most stocks over the past year (RS Rating 35).",
        ),
        ({"rs_rating": None}, "There's no strength rating for it yet, and its industry"),
    ],
)
def test_each_situation_in_words(changes: dict[str, object], sentence: str) -> None:
    assert sentence in plain_summary(replace(FACTS, **changes), READY)  # type: ignore[arg-type]
