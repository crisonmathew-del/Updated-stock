"""Pick the configured adapter for each provider role (`*_PROVIDER` settings)."""

from app.core.config import get_settings
from app.providers.base import (
    FilingsProvider,
    FundamentalsProvider,
    PriceProvider,
    ProviderNotConfiguredError,
    ReferenceProvider,
    StreamProvider,
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


def filings_provider() -> FilingsProvider:
    """SEC EDGAR for the daily index and Form 4 (needs SEC_USER_AGENT whatever the
    fundamentals provider is; a paid provider adds 13F holdings later)."""
    from app.providers.sec_edgar import SecFilingsProvider

    return SecFilingsProvider()


def live_stream_provider() -> StreamProvider | None:
    """The live feed (STREAM_PROVIDER=alpaca), or None. Replay (STREAM_PROVIDER=replay) is
    built by the streamer, which knows the session's previous closes."""
    settings = get_settings()
    if settings.stream_provider != "alpaca":
        return None
    if settings.alpaca_api_key_id is None or settings.alpaca_api_secret_key is None:
        raise ProviderNotConfiguredError(
            "STREAM_PROVIDER=alpaca needs ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
            "(free keys from an Alpaca account). Set them in .env, or STREAM_PROVIDER=none."
        )
    from app.providers.alpaca import AlpacaStream

    return AlpacaStream(
        settings.alpaca_api_key_id.get_secret_value(),
        settings.alpaca_api_secret_key.get_secret_value(),
        settings.alpaca_feed,
    )
