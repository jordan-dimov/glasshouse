"""The compute zone: curves, shaping, validation, MTM and P&L.

Plain Python over the app schema. Results that matter (an MTM, a curve
registration) are proposed back to the commit zone as governed claims;
nothing here writes governed state directly. Money and quantities are
Decimal from strings, never via float.
"""
