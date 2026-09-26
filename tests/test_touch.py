"""Check touch handling, the detail view's data, and head to head."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import datetime
import sys

import harness  # noqa: F401
import fs

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


def ok(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}{(': ' + detail) if detail else ''}")


NOW = int(datetime.datetime(2026, 9, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
clock = {"t": NOW}
fs.now_unix = lambda: clock["t"]
fs.tz_offset = 3600

CITY, RIVAL = 65, 61


def fixture(fid, ts, status, gh=None, ga=None, gh_ht=None, ga_ht=None, fav=CITY):
    return {"id": fid, "ts": ts, "date": "", "status": status, "elapsed": None,
            "league": "Premier League", "home": "Man City", "home_id": CITY,
            "away": "Chelsea", "away_id": RIVAL, "gh": gh, "ga": ga,
            "gh_ht": gh_ht, "ga_ht": ga_ht, "winner": None, "matchday": 5,
            "stage": "REGULAR_SEASON", "referee": "A Referee",
            "fav": fav, "finished_at": None}


# --- match_state -------------------------------------------------------------
check("scheduled is upcoming", fs.match_state(fixture(1, NOW, "TIMED")), "upcoming")
check("in play is live", fs.match_state(fixture(1, NOW, "IN_PLAY")), "live")
check("paused is live", fs.match_state(fixture(1, NOW, "PAUSED")), "live")
check("finished is played", fs.match_state(fixture(1, NOW, "FINISHED")), "played")
check("postponed is upcoming", fs.match_state(fixture(1, NOW, "POSTPONED")), "upcoming")

# --- goal_split: what we can honestly infer from two scores ------------------
check("5-3 with 3-2 at the break",
      fs.goal_split(fixture(1, NOW, "FINISHED", 5, 3, 3, 2)), ((3, 2), (2, 1)))
check("goalless", fs.goal_split(fixture(1, NOW, "FINISHED", 0, 0, 0, 0)), ((0, 0), (0, 0)))
check("all in the first half",
      fs.goal_split(fixture(1, NOW, "FINISHED", 2, 0, 2, 0)), ((2, 0), (0, 0)))
check("all in the second half",
      fs.goal_split(fixture(1, NOW, "FINISHED", 2, 1, 0, 0)), ((0, 0), (2, 1)))
# No half time published: everything lands in one band rather than going missing.
check("no half time score",
      fs.goal_split(fixture(1, NOW, "FINISHED", 3, 1, None, None)), ((3, 1), (0, 0)))
# A score that shrinks after half time would be nonsense; clamp rather than
# render negative goals.
check("never negative",
      fs.goal_split(fixture(1, NOW, "FINISHED", 1, 0, 2, 0)), ((2, 0), (0, 0)))

for gh in range(5):
    for ga in range(5):
        for hh in range(gh + 1):
            for ha in range(ga + 1):
                (a, b), (c, d) = fs.goal_split(fixture(1, NOW, "FINISHED", gh, ga, hh, ha))
                ok(f"dots add up for {gh}-{ga} (ht {hh}-{ha})",
                   a + c == gh and b + d == ga, f"{a}+{c} / {b}+{d}")

# --- head to head is read from your club's side ------------------------------
MEETINGS = [
    {"when": "1 Jan 26", "home": "MCI", "away": "CHE", "home_id": CITY, "gh": 2, "ga": 0, "winner": "HOME"},
    {"when": "1 Jan 25", "home": "CHE", "away": "MCI", "home_id": RIVAL, "gh": 1, "ga": 3, "winner": "AWAY"},
    {"when": "1 Jan 24", "home": "MCI", "away": "CHE", "home_id": CITY, "gh": 1, "ga": 1, "winner": "DRAW"},
    {"when": "1 Jan 23", "home": "CHE", "away": "MCI", "home_id": RIVAL, "gh": 2, "ga": 1, "winner": "HOME"},
]
home_fixture = fixture(1, NOW, "TIMED")
check("perspective is your club when it is at home", fs.h2h_side(home_fixture), CITY)
check("record from City's view", fs.h2h_record(home_fixture, MEETINGS), (2, 1, 1))

away_fixture = dict(home_fixture, home_id=RIVAL, away_id=CITY,
                    home="Chelsea", away="Man City")
check("perspective follows your club away", fs.h2h_side(away_fixture), CITY)
check("same record when your club is away",
      fs.h2h_record(away_fixture, MEETINGS), (2, 1, 1))

neutral = dict(home_fixture, fav=999)
check("falls back to the home side", fs.h2h_side(neutral), CITY)
check("no meetings is an empty record", fs.h2h_record(home_fixture, []), (0, 0, 0))

total = sum(fs.h2h_record(home_fixture, MEETINGS))
check("record accounts for every meeting", total, len(MEETINGS))

# --- hitting a card ----------------------------------------------------------
target = fixture(7, NOW + 86400, "TIMED")
other = fixture(8, NOW + 172800, "TIMED")
del fs.touch_targets[:]
fs.touch_targets.append((16, 100, 448, 60, target))
fs.touch_targets.append((16, 170, 448, 60, other))

check("inside the first card", fs.fixture_at(200, 130), target)
check("inside the second card", fs.fixture_at(200, 200), other)
check("on the top edge counts", fs.fixture_at(200, 100), target)
check("on the bottom edge counts", fs.fixture_at(200, 160), target)
check("in the gap between cards", fs.fixture_at(200, 165), None)
check("left of the cards", fs.fixture_at(4, 130), None)
check("below everything", fs.fixture_at(200, 400), None)

# --- the touch state machine -------------------------------------------------
touch = {"v": (0, 0, False)}
fs.presto.touch_poll = lambda: None
fs.presto.touch_a = (0, 0, False)


def press(x, y):
    fs.presto.touch_a = (x, y, True)
    fs.handle_touch()


def move(x, y):
    fs.presto.touch_a = (x, y, True)
    fs.handle_touch()


def release():
    fs.presto.touch_a = (fs.touch_last_y, fs.touch_last_y, False)
    fs.handle_touch()


def to_dashboard():
    fs.view = "dashboard"
    fs.detail_fixture = None
    fs.detail_scroll = 0
    fs.detail_height = 0
    fs.h2h_wanted = None
    fs.touch_down = False
    fs.touch_moved = False
    fs.detail_h2h.clear()


# a tap on a card opens it
to_dashboard()
press(200, 130)
release()
check("tap opens the detail view", fs.view, "detail")
check("opens the card that was tapped", fs.detail_fixture, target)
check("queues its head to head", fs.h2h_wanted, target["id"])

# a tap on the back bar closes it
press(40, 20)
release()
check("tapping back returns to the dashboard", fs.view, "dashboard")
check("and forgets the fixture", fs.detail_fixture, None)

# a tap in empty space does nothing
to_dashboard()
press(200, 400)
release()
check("tapping empty space stays put", fs.view, "dashboard")

# a drag is not a tap
to_dashboard()
press(200, 130)
move(200, 130 - fs.TAP_SLOP - 10)
release()
check("dragging does not open a card", fs.view, "dashboard")

# --- scrolling ---------------------------------------------------------------
to_dashboard()
press(200, 130)
release()
check("detail open for scrolling", fs.view, "detail")
fs.detail_height = (fs.HEIGHT - fs.DETAIL_TOP) + 200      # 200px of overflow
fs.detail_scroll = 0

press(240, 400)
move(240, 300)                       # dragged up by 100
release()
check("drag scrolls the content", fs.detail_scroll, 100)

# cannot scroll past the end
press(240, 400)
move(240, 100)
release()
check("clamped at the bottom", fs.detail_scroll, 200)

# cannot scroll above the top
press(240, 100)
move(240, 460)
release()
check("clamped at the top", fs.detail_scroll, 0)

# with nothing to scroll, dragging leaves the offset alone
fs.detail_height = 100
fs.detail_scroll = 0
press(240, 400)
move(240, 200)
release()
check("no scrolling when it all fits", fs.detail_scroll, 0)

# --- the detail view times out -----------------------------------------------
to_dashboard()
press(200, 130)
release()
check("open before the timeout", fs.view, "detail")
clock["t"] += fs.DETAIL_TIMEOUT_S - 5
ok("still open just before the timeout", fs.now_unix() - fs.detail_opened_at < fs.DETAIL_TIMEOUT_S)
clock["t"] += 10
ok("past the timeout", fs.now_unix() - fs.detail_opened_at > fs.DETAIL_TIMEOUT_S)
fs.close_detail()
check("closes cleanly", fs.view, "dashboard")

# touching keeps it awake
to_dashboard()
press(200, 130)
release()
clock["t"] += fs.DETAIL_TIMEOUT_S - 5
press(240, 300)
release()
ok("a touch resets the idle clock",
   fs.now_unix() - fs.detail_opened_at < 5, str(fs.now_unix() - fs.detail_opened_at))

# --- head to head parsing ----------------------------------------------------
sample = {"matches": [
    {"utcDate": "2026-02-08T16:30:00Z",
     "homeTeam": {"id": 64, "tla": "LIV", "shortName": "Liverpool"},
     "awayTeam": {"id": 65, "tla": "MCI", "shortName": "Man City"},
     "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 2}}},
    {"utcDate": "2025-11-09T16:30:00Z",
     "homeTeam": {"id": 65, "tla": "MCI", "shortName": "Man City"},
     "awayTeam": {"id": 64, "tla": "LIV", "shortName": "Liverpool"},
     "score": {"winner": "DRAW", "fullTime": {"home": 1, "away": 1}}},
    # An unplayed fixture must be skipped rather than drawn as "None-None".
    {"utcDate": "2027-01-01T16:30:00Z",
     "homeTeam": {"id": 64, "tla": "LIV", "shortName": "Liverpool"},
     "awayTeam": {"id": 65, "tla": "MCI", "shortName": "Man City"},
     "score": {"winner": None, "fullTime": {"home": None, "away": None}}},
]}
fs.api_get = lambda endpoint, params=None: sample
parsed = fs.fetch_h2h(600)
check("skips unplayed meetings", len(parsed), 2)
check("date formatted", parsed[0]["when"], "8 Feb 26")
check("uses the three letter code", parsed[0]["home"], "LIV")
check("score carried", (parsed[0]["gh"], parsed[0]["ga"]), (1, 2))
check("winner normalised", parsed[0]["winner"], "AWAY")
check("draw normalised", parsed[1]["winner"], "DRAW")

# a failure records an empty list rather than retrying forever
def boom(endpoint, params=None):
    raise fs.ApiError("nope")


fs.api_get = boom
fs.h2h_wanted = 601
fs.state["recent_calls"] = []
fs.poll_h2h()
check("a failed lookup is remembered as empty", fs.detail_h2h.get(601), [])
check("and is not retried", fs.h2h_wanted, None)

# it defers when the rate limit is close
fs.api_get = lambda endpoint, params=None: sample
fs.h2h_wanted = 602
fs.state["recent_calls"] = [clock["t"]] * (fs.REQUESTS_PER_MINUTE - 1)
fs.poll_h2h()
check("defers near the rate limit", 602 in fs.detail_h2h, False)
check("stays queued for later", fs.h2h_wanted, 602)

fs.state["recent_calls"] = []
fs.poll_h2h()
check("fetched once there is room", 602 in fs.detail_h2h, True)

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    seen = set()
    for f in fails:
        if f not in seen:
            seen.add(f)
            print("  -", f)
    sys.exit(1)
print("All touch and detail checks passed.")
