"""Base and pattern detection (spec §6.7) and the pattern-based entry events (§6.8).

Everything here is a pure function of one ticker's bars and indicators up to the as-of
session (`Bars` is cut at that session before any detector runs), so a detection can never
see the future. See `detect.detect_patterns` for the entry point.
"""
