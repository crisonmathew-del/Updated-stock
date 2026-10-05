"""ORM models. Importing this package registers every table on `Base.metadata` (Alembic relies
on that for autogenerate)."""

from app.models.analytics import (
    GroupRankDaily,
    IndicatorDaily,
    IndustryGroup,
    MarketBreadthDaily,
    MarketRegimeDaily,
)
from app.models.fundamentals import (
    EarningsEvent,
    FundamentalGrade,
    FundamentalsAnnual,
    FundamentalsQuarterly,
    InsiderTransaction,
)
from app.models.market_data import CorporateAction, DailyBar, SharesOutstanding
from app.models.ops import DataQualityIssue, JobRun
from app.models.patterns import Pattern, PatternReview
from app.models.setting import Setting
from app.models.setups import ScanProgress, Setup, SetupTransition, Signal, SignalOutcome
from app.models.ticker import Ticker, TickerType
from app.models.user import User
from app.models.workspace import SavedScreen, StockNote, Watchlist, WatchlistItem

__all__ = [
    "CorporateAction",
    "DailyBar",
    "DataQualityIssue",
    "EarningsEvent",
    "FundamentalGrade",
    "FundamentalsAnnual",
    "FundamentalsQuarterly",
    "GroupRankDaily",
    "IndicatorDaily",
    "IndustryGroup",
    "InsiderTransaction",
    "JobRun",
    "MarketBreadthDaily",
    "MarketRegimeDaily",
    "Pattern",
    "PatternReview",
    "SavedScreen",
    "ScanProgress",
    "Setting",
    "Setup",
    "SetupTransition",
    "SharesOutstanding",
    "Signal",
    "SignalOutcome",
    "StockNote",
    "Ticker",
    "TickerType",
    "User",
    "Watchlist",
    "WatchlistItem",
]
