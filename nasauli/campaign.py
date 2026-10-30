"""The test matrix the flights are meant to cover, per drone.

Every combination of altitude x speed is to be flown with each payload, twice (two sets). The website's
coverage tables (one per set) show how many payloads each altitude/speed cell has so far.
"""

ALTITUDES_M = (25, 50, 75)
SPEEDS_M_S = {
    "TAROT_450": (5, 8, 11, 14),
    "AURELIA_X6": (5, 8, 11, 14),
}
PAYLOADS_G = (0, 180, 360)
SETS = 2

# A leg is a stretch of the cruise flown at one speed (groundspeed within SPEED_TOL of it) and one altitude
# (within ALT_TOL_M of a matrix altitude), lasting at least MIN_LEG_S in total.
SPEED_TOL = 0.15  # fraction of the speed, at least 1.5 m/s
ALT_TOL_M = 7.0
MIN_LEG_S = 15.0
