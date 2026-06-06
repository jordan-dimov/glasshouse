"""Fixed-price physical power: the first product pack.

European power delivery at native granularity (15-minute MTUs on the
continent, half-hours in GB), DST-correct (periods are UTC instants;
92/100 and 46/50-period days fall out of the timezone arithmetic, they are
never special-cased).
"""
