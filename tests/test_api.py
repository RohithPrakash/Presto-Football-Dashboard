"""Exercise the football-data.org client and the full match lifecycle."""
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


NOW = int(datetime.datetime(2026, 9, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
clock = {"t": NOW}
fs.now_unix = lambda: clock["t"]
fs.DEBUG = False
fs.tz_offset = 3600

KICKOFF = NOW + 3600
CITY, CHELSEA = 65, 61
CREST = "https://crests.football-data.org/{}.png"


def iso(ts):
    c = fs.civil_from_unix(ts)
    return "%04d-%02d-%02dT%02d:%02d:00Z" % (c[0], c[1], c[2], c[3], c[4])


def match(mid, ts, status, minute=None, gh=None, ga=None,
          home=CITY, away=CHELSEA, comp="Premier League"):
    names = {CITY: "Man City", CHELSEA: "Chelsea"}
    return {
        "id": mid,
        "utcDate": iso(ts),
        "status": status,
        "minute": minute,
        "competition": {"id": 2021, "name": comp},
        "homeTeam": {"id": home, "name": names[home], "shortName": names[home],
                     "crest": CREST.format(home)},
        "awayTeam": {"id": away, "name": names[away], "shortName": names[away],
                     "crest": CREST.format(away)},
        "score": {"fullTime": {"home": gh, "away": ga}},
    }


calls = []
script = {}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self): return self._payload
    def close(self): pass


def fake_get(url, headers=None):
    calls.append(url)
    check("auth header sent", headers.get("X-Auth-Token"), "tok")
    for key, payload in script.items():
        if key.startswith("_"):
            continue
        if key in url:
            return FakeResponse(payload)
    raise AssertionError("unscripted URL: " + url)


sys.modules["requests"].get = fake_get

fs.state["team_ids"] = {}
fs.state["results"] = {}
fs.state["crests"] = {}
fs.state["recent_calls"] = []
fs.FAVOURITE_TEAMS = [CITY]

# --- the date window is built from today, not a "next" shortcut -------------
upcoming = [match(900 + i, KICKOFF + i * 7 * 86400, "TIMED") for i in range(5)]
script.clear()
script["/teams/65/matches"] = {"matches": upcoming}

fs.next_schedule_fetch = 0
fs.refresh_schedule(force=True)

check("one call per team", len(calls), 1)
check("asks the team matches endpoint", "/v4/teams/65/matches" in calls[0], True)
check("sends dateFrom", "dateFrom=2026-09-19" in calls[0], True)
check("sends dateTo", "dateTo=" in calls[0], True)
check("no next parameter", "next=" not in calls[0], True)

check("five fixtures loaded", len(fs.schedule[CITY]), 5)
check("crests captured", fs.state["crests"].get(CITY), CREST.format(CITY))
check("nothing featured yet", fs.featured_fixture()[0], None)
fs.draw_screen()

# --- times convert from UTC using tz_offset ---------------------------------
first = fs.schedule[CITY][0]
check("kickoff parsed to unix", first["ts"], KICKOFF)
when = fs.fixture_when(first)
check("local time applied (+1h)", when[1], "14:00")

# --- badges are queued with their crest URLs --------------------------------
fs.queue_badges()
wanted = fs.badges.requested
check("badges queued as (id, url)", isinstance(wanted[0], tuple), True)
check("badge url is the crest", wanted[0][1].startswith("https://crests."), True)

# --- live: only teams actually playing get polled ---------------------------
clock["t"] = KICKOFF + 600
script.clear()
script["/teams/65/matches"] = {"matches": [
    match(900, KICKOFF, "IN_PLAY", minute=10, gh=1, ga=0)] + upcoming[1:]}
fs.next_live_poll = 0
before = len(calls)
fs.poll_live()
check("live poll made one call", len(calls) - before, 1)

feat, live = fs.featured_fixture()
check("live match featured", (feat["id"], live), (900, True))
check("live score", (feat["gh"], feat["ga"]), (1, 0))
check("minute carried through", feat["elapsed"], 10)
fs.draw_screen()

# a team with no match on must not be polled
fs.schedule[CHELSEA] = [dict(fs.schedule[CITY][2], fav=CHELSEA)]
fs.next_live_poll = 0
before = len(calls)
fs.poll_live()
check("idle team not polled", len(calls) - before, 1)
del fs.schedule[CHELSEA]

# --- PAUSED (half time) is still in play ------------------------------------
script["/teams/65/matches"] = {"matches": [
    match(900, KICKOFF, "PAUSED", minute=45, gh=1, ga=1)] + upcoming[1:]}
fs.next_live_poll = 0
fs.poll_live()
feat, live = fs.featured_fixture()
check("half time still featured live", (feat["status"], live), ("PAUSED", True))
fs.draw_screen()

# --- full time: stored, and the next four still listed ----------------------
clock["t"] = KICKOFF + 2 * 3600
script["/teams/65/matches"] = {"matches": [
    match(900, KICKOFF, "FINISHED", minute=90, gh=2, ga=1)] + upcoming[1:]}
fs.next_live_poll = 0
fs.next_schedule_fetch = 0
fs.poll_live()

check("dropped from live watch", 900 in fs.live_fixtures, False)
check("result stored", "900" in fs.state["results"], True)
feat, live = fs.featured_fixture()
check("finished match featured", (feat["id"], live), (900, False))
check("final score held", (feat["gh"], feat["ga"]), (2, 1))
upcoming_now = [f for f in fs.all_known_fixtures()
                if f["status"] not in fs.FINISHED and f["id"] != 900]
check("four fixtures behind the result", len(upcoming_now) >= 4, True)
fs.draw_screen()

# --- the 24h hold runs from the final whistle -------------------------------
clock["t"] = KICKOFF + 25 * 3600
check("held 23h after full time", fs.featured_fixture()[0]["id"], 900)
clock["t"] = KICKOFF + 27 * 3600
check("dropped 25h after full time", fs.featured_fixture()[0], None)
check("purged from state", fs.state["results"], {})

# --- a finished match in the window is picked up without an extra call ------
clock["t"] = KICKOFF + 3 * 3600
fs.state["results"] = {}
fs.live_fixtures.clear()
script["/teams/65/matches"] = {"matches": [
    match(950, KICKOFF, "FINISHED", minute=90, gh=3, ga=0)] + upcoming[1:]}
fs.next_schedule_fetch = 0
before = len(calls)
fs.refresh_schedule(force=True)
check("still just one call", len(calls) - before, 1)
check("recent result harvested from the same call", "950" in fs.state["results"], True)
check("finished match kept out of upcoming",
      all(f["status"] not in fs.FINISHED for f in fs.schedule[CITY]), True)

# --- postponed matches are dropped, not watched -----------------------------
clock["t"] = KICKOFF + 600
fs.live_fixtures.clear()
fs.state["results"] = {}
fs.schedule[CITY] = [fs.slim_fixture(match(960, KICKOFF, "TIMED"), CITY)]
script["/teams/65/matches"] = {"matches": [match(960, KICKOFF, "POSTPONED")]}
fs.next_live_poll = 0
fs.next_schedule_fetch = 0
fs.poll_live()
check("postponed not watched", 960 in fs.live_fixtures, False)
check("postponed not stored as a result", "960" in fs.state["results"], False)

# --- rate limiting refuses rather than getting a 429 ------------------------
fs.state["recent_calls"] = [clock["t"]] * fs.REQUESTS_PER_MINUTE
try:
    fs.api_get("/teams/65/matches")
    fails.append("rate limit: expected ApiError")
except fs.ApiError as e:
    check("rate limit refuses locally", "Rate limit" in str(e), True)
fs.state["recent_calls"] = []

# --- server errors surface clearly ------------------------------------------
for code, expect in ((429, "Rate limited"), (403, "Token rejected"),
                     (401, "Token rejected"), (500, "HTTP 500")):
    def erroring(url, headers=None, _code=code):
        calls.append(url)
        return FakeResponse({}, status=_code)
    sys.modules["requests"].get = erroring
    fs.state["recent_calls"] = []
    try:
        fs.api_get("/teams/65/matches")
        fails.append(f"HTTP {code}: expected ApiError")
    except fs.ApiError as e:
        check(f"HTTP {code} reported", expect in str(e), True)
sys.modules["requests"].get = fake_get

# a message field in a 200 body is an error too
def message_body(url, headers=None):
    calls.append(url)
    return FakeResponse({"message": "The resource you are looking for does not exist."})


sys.modules["requests"].get = message_body
fs.state["recent_calls"] = []
try:
    fs.api_get("/teams/65/matches")
    fails.append("message body: expected ApiError")
except fs.ApiError as e:
    check("message body surfaced", "does not exist" in str(e), True)
sys.modules["requests"].get = fake_get

# --- missing token is reported, not silently retried ------------------------
saved = fs.FOOTBALL_DATA_TOKEN
fs.FOOTBALL_DATA_TOKEN = ""
fs.state["recent_calls"] = []
try:
    fs.api_get("/teams/65/matches")
    fails.append("no token: expected ApiError")
except fs.ApiError as e:
    check("missing token reported", "FOOTBALL_DATA_TOKEN" in str(e), True)
fs.FOOTBALL_DATA_TOKEN = saved

# --- team name resolution walks the free competitions -----------------------
fs.state["team_ids"] = {}
script.clear()
script["/competitions/PL/teams"] = {"teams": [
    {"id": 57, "name": "Arsenal FC", "shortName": "Arsenal", "tla": "ARS",
     "crest": CREST.format(57)},
    {"id": CITY, "name": "Manchester City FC", "shortName": "Man City",
     "tla": "MCI", "crest": CREST.format(CITY)},
]}
before = len(calls)
check("resolved by short name", fs.resolve_team("Man City"), CITY)
check("one lookup call", len(calls) - before, 1)
check("cached second time", fs.resolve_team("Man City"), CITY)
check("no extra call", len(calls) - before, 1)
check("resolved by full name", fs.resolve_team("Arsenal FC"), 57)
check("resolved by tla", fs.resolve_team("MCI"), CITY)
check("crest learned during lookup", fs.state["crests"].get(57), CREST.format(57))
check("numeric id costs nothing", fs.resolve_team(99), 99)

# --- a failed refresh backs off instead of hammering ------------------------
def failing(url, headers=None):
    calls.append(url)
    raise OSError("network down")


fs.FAVOURITE_TEAMS = [CITY]
fs.next_schedule_fetch = 0
fs.state["recent_calls"] = []
sys.modules["requests"].get = failing
before = len(calls)
fs.refresh_schedule()
after_first = len(calls) - before
for _ in range(120):
    fs.refresh_schedule()
check("failed refresh tried once", after_first, 1)
check("no tight retry loop", len(calls) - before, 1)

clock["t"] += fs.SCHEDULE_RETRY_S + 1
sys.modules["requests"].get = fake_get
script.clear()
script["/teams/65/matches"] = {"matches": [match(970, clock["t"] + 86400, "TIMED")]}
fs.state["recent_calls"] = []
fs.refresh_schedule()
check("resumes after the backoff", len(calls) - before, 2)
check("success schedules the long interval",
      fs.next_schedule_fetch - clock["t"], fs.SCHEDULE_REFRESH_S)

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    seen = set()
    for f in fails:
        if f not in seen:
            seen.add(f)
            print("  -", f)
    sys.exit(1)
print(f"All API and lifecycle checks passed ({len(calls)} mocked calls).")
