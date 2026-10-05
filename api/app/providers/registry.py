"""Pick the configured adapter for each provider role (`*_PROVIDER` settings)."""

from app.core.config import get_settings
from app.providers.base import (
    FundamentalsProvider,
    PriceProvider,
    ProviderNotConfiguredError,
    ReferenceProvider,
)


def _not_built(role: str, name: str, key: str) -> ProviderNotConfiguredError:
    return ProviderNotConfiguredError(
        f"{role}={name} isn't available yet: the adapter is added once {key} is provided. "
        f"Remove {role} from .env to use the keyless default."
    )


def price_provider() -> PriceProvider:
    name = get_settings().price_provider
    if name == "yfinance":
        from app.providers.yfinance_dev import YFinanceDevProvider

        return YFinanceDevProvider()
    raise _not_built("PRICE_PROVIDER", name, "MASSIVE_API_KEY")


def reference_provider() -> ReferenceProvider:
    # Massive's reference endpoint replaces this when PRICE_PROVIDER=massive is built.
    from app.providers.nasdaq_trader import NasdaqTraderProvider

    return NasdaqTraderProvider()


def fundamentals_provider() -> FundamentalsProvider:
    name = get_settings().fundamentals_provider
    if name == "sec_edgar":
        from app.providers.sec_edgar import SecEdgarProvider

        return SecEdgarProvider()
    raise _not_built("FUNDAMENTALS_PROVIDER", name, "FMP_API_KEY")
