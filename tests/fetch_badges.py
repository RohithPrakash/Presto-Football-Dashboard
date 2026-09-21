#!/usr/bin/env python3
"""Download a corpus of real club crests for the badge decoder tests.

These come from football-data.org's CDN, which needs no token and counts
against no quota. They are not committed to the repo - run this once:

    python3 tests/fetch_badges.py
"""
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "badges")
URL = "https://crests.football-data.org/{}.png"

# A spread across the free-tier competitions, including a few crests that are
# not square - the decoder has to preserve aspect ratio.
TEAM_IDS = [
    65, 57, 61, 64, 66, 73, 67,                           # Premier League
    78, 81, 86, 94, 95,                                   # La Liga
    98, 100, 108, 109, 113,                               # Serie A
    4, 5, 18, 721,                                        # Bundesliga
    516, 524, 548,                                        # Ligue 1
    1765, 1783, 6684,                                     # Brazil
    503, 498,                                             # Primeira Liga
]


def main():
    os.makedirs(OUT, exist_ok=True)
    got = skipped = failed = 0
    for team_id in TEAM_IDS:
        path = os.path.join(OUT, "%d.png" % team_id)
        if os.path.exists(path):
            skipped += 1
            continue
        try:
            with urllib.request.urlopen(URL.format(team_id), timeout=20) as r:
                data = r.read()
            if not data.startswith(b"\x89PNG"):
                raise ValueError("not a PNG")
            with open(path, "wb") as f:
                f.write(data)
            got += 1
        except Exception as e:                    # noqa: BLE001 - best effort
            print("  could not fetch %d: %s" % (team_id, e))
            failed += 1
    print("fetched %d, already present %d, failed %d -> %s"
          % (got, skipped, failed, OUT))


if __name__ == "__main__":
    main()
