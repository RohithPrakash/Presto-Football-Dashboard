"""Check the date maths, formatting and call-budget logic."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import calendar
import datetime
import random
import sys
import harness  # noqa: F401  (installs stubs and loads the module)
import fs

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


# --- days_from_civil / civil_from_unix round-trip against CPython ------------
random.seed(7)
for _ in range(3000):
    y = random.randint(1971, 2060)
    m = random.randint(1, 12)
    d = random.randint(1, calendar.monthrange(y, m)[1])
    hh, mm = random.randint(0, 23), random.randint(0, 59)

    dt = datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc)
    unix = int(dt.timestamp())

    check(f"days_from_civil {y}-{m}-{d}", fs.days_from_civil(y, m, d) * 86400, unix - hh * 3600 - mm * 60)
    check(f"civil_from_unix {unix}", fs.civil_from_unix(unix)[:5], (y, m, d, hh, mm))
    # weekday: python Monday=0; ours Sunday=0
    check(f"weekday {y}-{m}-{d}", fs.DAY_NAMES[fs.civil_from_unix(unix)[5]], dt.strftime("%a"))
    check(f"weekday via civil {y}-{m}-{d}",
          fs.DAY_NAMES[(fs.days_from_civil(y, m, d) + 4) % 7], dt.strftime("%a"))

# --- parse_iso ---------------------------------------------------------------
check("parse_iso +01:00", fs.parse_iso("2026-09-21T15:00:00+01:00"), (2026, 9, 21, 15, 0, 3600))
check("parse_iso -04:00", fs.parse_iso("2026-01-02T20:30:00-04:00"), (2026, 1, 2, 20, 30, -14400))
check("parse_iso +05:30", fs.parse_iso("2026-01-02T20:30:00+05:30"), (2026, 1, 2, 20, 30, 19800))
check("parse_iso Z", fs.parse_iso("2026-01-02T20:30:00Z"), (2026, 1, 2, 20, 30, 0))

# --- accent folding (the .af font only has the 95 ASCII glyphs) --------------
for src, want in (
        ("Campeonato Brasileiro Serie A", "Campeonato Brasileiro Serie A"),
        ("Campeonato Brasileiro S\u00e9rie A", "Campeonato Brasileiro Serie A"),
        ("Atl\u00e9tico Madrid", "Atletico Madrid"),
        ("Bayern M\u00fcnchen", "Bayern Munchen"),
        ("Be\u015fikta\u015f", "Besiktas"),
        ("FC K\u00f8benhavn", "FC Kobenhavn"),
        ("Vit\u00f3ria", "Vitoria"),
        ("Man City", "Man City")):
    check("to_ascii %r" % src, fs.to_ascii(src), want)

for src in ("Atl\u00e9tico", "\u65e5\u672c", "", "Be\u015fikta\u015f"):
    out = fs.to_ascii(src)
    check("to_ascii output drawable %r" % src,
          all(" " <= c <= "~" for c in out) and len(out) > 0, True)

# --- formatting --------------------------------------------------------------
check("format_time 24h", fs.format_time(15, 5), "15:05")
fs.USE_24_HOUR = False
check("format_time 12h pm", fs.format_time(15, 5), "3:05pm")
check("format_time midnight", fs.format_time(0, 5), "12:05am")
check("format_time noon", fs.format_time(12, 0), "12:00pm")
fs.USE_24_HOUR = True

check("duration mins", fs.format_duration(45 * 60), "in 45m")
check("duration hours", fs.format_duration(3 * 3600 + 15 * 60), "in 3h 15m")
check("duration days", fs.format_duration(50 * 3600), "in 2d 2h")
check("duration negative", fs.format_duration(-10), "in 0m")

# --- epoch shift -------------------------------------------------------------
# CPython's time.gmtime(0) is 1970, so the shift must be 0 here; on the RP2 it
# would be 946684800. Verify the constant itself is right either way.
check("epoch shift on cpython", fs.EPOCH_SHIFT, 0)
check("2000 epoch constant",
      int(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc).timestamp()), 946684800)

# --- timezone calibration ----------------------------------------------------
NOW = int(datetime.datetime(2026, 9, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
fs.now_unix = lambda: NOW


def fixture(fid, ts, status="TIMED", fav=65, home="Man City", away="Chelsea",
            gh=None, ga=None, league="Premier League", date=None):
    if date is None:
        c = fs.civil_from_unix(ts)
        date = "%04d-%02d-%02dT%02d:%02d:00Z" % (c[0], c[1], c[2], c[3], c[4])
    return {"id": fid, "ts": ts, "date": date, "status": status, "elapsed": None,
            "league": league, "home": home, "home_id": 65 if home == "Man City" else 61,
            "away": away, "away_id": 61 if away == "Chelsea" else 65,
            "gh": gh, "ga": ga, "fav": fav, "finished_at": None}


check("tz_offset from UTC_OFFSET", fs.tz_offset, 3600)

# iso_to_unix must round-trip UTC timestamps exactly
for probe in (NOW, NOW + 86399, NOW - 12345):
    c = fs.civil_from_unix(probe)
    iso = "%04d-%02d-%02dT%02d:%02d:00Z" % (c[0], c[1], c[2], c[3], c[4])
    check("iso_to_unix %s" % iso, fs.iso_to_unix(iso), probe - (probe % 60))
check("iso_to_unix honours an offset",
      fs.iso_to_unix("2026-09-21T15:00:00+02:00"),
      fs.iso_to_unix("2026-09-21T13:00:00Z"))
fs.tz_offset = 3600

# --- fixture_when ------------------------------------------------------------
check("fixture_when today", fs.fixture_when(fixture(3, NOW + 3600))[0], "Today")
check("fixture_when time", fs.fixture_when(fixture(3, NOW + 3600))[1], "14:00")
check("fixture_when tomorrow", fs.fixture_when(fixture(4, NOW + 26 * 3600))[0], "Tomorrow")
check("fixture_when dated", fs.fixture_when(fixture(5, NOW + 6 * 86400))[0], "Sun 27 Sep")

# --- fixture_label -----------------------------------------------------------
check("label home", fs.fixture_label(fixture(6, NOW, home="Man City", away="Chelsea")), ("Chelsea", True))
away_fx = fixture(7, NOW, home="Chelsea", away="Man City")
away_fx["home_id"], away_fx["away_id"] = 61, 65
check("label away", fs.fixture_label(away_fx), ("Chelsea", False))

# --- is_watchable ------------------------------------------------------------
check("watchable at kickoff", fs.is_watchable(fixture(8, NOW), NOW), True)
check("watchable 4m before", fs.is_watchable(fixture(9, NOW + 240), NOW), True)
check("not watchable 10m before", fs.is_watchable(fixture(10, NOW + 600), NOW), False)
check("watchable 3h after", fs.is_watchable(fixture(11, NOW - 3 * 3600), NOW), True)
check("not watchable 4h after", fs.is_watchable(fixture(12, NOW - 4 * 3600), NOW), False)
check("not watchable when FINISHED", fs.is_watchable(fixture(13, NOW, status="FINISHED"), NOW), False)
check("not watchable when POSTPONED", fs.is_watchable(fixture(14, NOW, status="POSTPONED"), NOW), False)
for s in fs.IN_PLAY:
    check(f"watchable in play {s}", fs.is_watchable(fixture(15, NOW, status=s), NOW), True)

# --- rate limiting ------------------------------------------------------------
fs.state["recent_calls"] = []
check("no calls counted initially", fs.calls_in_last_minute(), 0)

# Calls inside the last minute count; older ones fall out of the window.
fs.state["recent_calls"] = [NOW - 5, NOW - 30, NOW - 59]
check("recent calls counted", fs.calls_in_last_minute(), 3)
fs.state["recent_calls"] = [NOW - 5, NOW - 61, NOW - 600]
check("stale calls expire", fs.calls_in_last_minute(), 1)
check("expiry prunes the list", len(fs.state["recent_calls"]), 1)
fs.state["recent_calls"] = []

# --- live polling interval ----------------------------------------------------
fs.schedule.clear()
fs.live_fixtures.clear()
fs.schedule[65] = [fixture(20, NOW + 3600), fixture(21, NOW + 7 * 86400)]
iv = fs.live_interval()
check("one team: interval in bounds", fs.MIN_LIVE_INTERVAL_S <= iv <= fs.MAX_LIVE_INTERVAL_S, True)
print("  one team interval:  ", iv, "s")

fs.schedule[61] = [fixture(22, NOW + 5 * 3600, fav=61)]
iv2 = fs.live_interval()
check("two teams poll no faster", iv2 >= iv, True)
print("  two team interval:  ", iv2, "s")

# Whatever the team count, polling must stay inside the documented rate limit.
for teams in (1, 2, 3, 5, 10):
    fs.schedule.clear()
    for i in range(teams):
        fs.schedule[i] = [fixture(200 + i, NOW + 3600)]
    interval = fs.live_interval()
    calls_per_minute = teams * 60.0 / interval
    check("%d teams stay under the rate limit (%.1f/min)" % (teams, calls_per_minute),
          calls_per_minute <= fs.REQUESTS_PER_MINUTE, True)
    check("%d teams: interval at least the minimum" % teams,
          interval >= fs.MIN_LIVE_INTERVAL_S, True)
print("  10-team interval:   ", fs.live_interval(), "s")

# --- a full match, polled at the computed interval ----------------------------
fs.schedule.clear()
fs.schedule[65] = [fixture(300, NOW + 3600)]
fs.state["recent_calls"] = []
kickoff = NOW + 3600
t, spent, worst = NOW, 0, 0
while t < kickoff + 2 * 3600:
    if fs.is_watchable(fs.schedule[65][0], t):
        fs.state["recent_calls"] = [c for c in fs.state["recent_calls"] if t - c < 60]
        fs.state["recent_calls"].append(t)
        worst = max(worst, len(fs.state["recent_calls"]))
        spent += 1
        t += fs.live_interval()
    else:
        t += 60
print(f"  a 2h match costs {spent} calls, peak {worst}/min")
check("peak rate inside the limit", worst <= fs.REQUESTS_PER_MINUTE, True)
fs.state["recent_calls"] = []
fs.schedule.clear()

fails_over = list(fails)
print()
if fails_over:
    print(f"FAILED ({len(fails_over)}):")
    for f in fails_over[:25]:
        print("  -", f)
    sys.exit(1)
print("All logic checks passed.")
