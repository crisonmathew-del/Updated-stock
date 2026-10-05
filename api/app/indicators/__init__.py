"""Technical indicators as Polars transformations.

Every function takes a frame of daily bars for one or many tickers, sorted by (ticker_id, date),
and adds columns computed per ticker with `.over("ticker_id")`. All windows look backwards only,
so a value for date D depends on bars up to and including D (no lookahead).
"""
