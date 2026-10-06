"""The backtest lab (spec §8.9, §11): the same rules the nightly scan runs, replayed over history
with only what was known at each session's close, then a portfolio simulation on top.

Stage 1 (app.backtest.tape, slow, cached): walk every stock forward one session at a time
through detection → setups → lifecycle, exactly as the EOD pipeline would have, and record each
session a setup waits below its pivot with a trade plan (the candidates), the sessions its
breakout was confirmed, and every signal. Stage 2 (app.backtest.engine, seconds): trade those
candidates with a portfolio's cash, slots, fills, costs and exits; app.backtest.metrics and
app.backtest.reports turn the trades and the equity curve into the report.
"""
