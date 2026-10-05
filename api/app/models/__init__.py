"""ORM models. Importing this package registers every table on `Base.metadata` (Alembic relies
on that for autogenerate)."""

from app.models.market_data import CorporateAction, DailyBar, SharesOutstanding
from app.models.ops import DataQualityIssue, JobRun
from app.models.setting import Setting
from app.models.ticker import Ticker, TickerType
from app.models.user import User

__all__ = [
    "CorporateAction",
    "DailyBar",
    "DataQualityIssue",
    "JobRun",
    "Setting",
    "SharesOutstanding",
    "Ticker",
    "TickerType",
    "User",
]
