# ICON monitoring
# NAME Football
# DESC Live scores and upcoming fixtures for your favourite teams.

"""
A football dashboard for the Pimoroni Presto.

Shows the next five matches for your favourite team(s) across every
competition they play in. When a match kicks off the panel switches to a live
score, and the final score stays on screen for 24 hours afterwards alongside
the next four fixtures.

Data comes from football-data.org, whose free tier covers the current season
for twelve competitions: the Premier League, La Liga, Serie A, Bundesliga,
Ligue 1, Eredivisie, Primeira Liga, Championship, Brazilian Serie A, the
Champions League, the Euros and the World Cup.

Setup
-----
You will need "Roboto-Medium.af" and "football_badges.py" saved to your Presto
alongside this script.

Get a free token at https://www.football-data.org/client/register, then add
the following to your secrets.py:

    WIFI_SSID = "your_ssid"
    WIFI_PASSWORD = "your_password"

    FOOTBALL_DATA_TOKEN = "your-token"

    # Team ids, or names which are looked up once per run. Names cost a call
    # or two at startup; ids are free. Find ids in the API's competition
    # listings, e.g. /v4/competitions/PL/teams.
    FAVOURITE_TEAMS = ["Manchester City"]

    # Hours ahead of UTC.
    UTC_OFFSET = 5.5

Call pacing
-----------
The free tier limits requests per minute (10) rather than per day, so the job
is spacing calls out rather than rationing a daily allowance:

* Fixture lists are refreshed every SCHEDULE_REFRESH_S (6h by default), one
  call per team. That same call also carries any recently finished match, so
  holding a final score on screen costs nothing extra.
* While a match is on, only the teams actually playing are polled, at most
  once a minute each.
* api_get refuses to fire if REQUESTS_PER_MINUTE calls have already gone out
  in the last minute, so the limit cannot be tripped even if something loops.
* A failed refresh backs off for SCHEDULE_RETRY_S instead of retrying hard.

Nothing is fetched while there is no match in progress; the countdown and
clock are drawn locally.

Ambient LEDs
------------
The seven LEDs around the screen follow the football. While a match is on the
home side's colour lights the left, the away side's the right, and the top
centre blends them. Afterwards the winner's colour takes the whole ring for
as long as the result is held - a draw keeps the two-sided split. With no
football on, the favourite clubs' colours cycle, one every fifteen minutes.

Colours are sampled from the crests as they are decoded, so nothing extra is
downloaded for them. Every change fades down, swaps, and fades back up.
Set LEDS_ENABLED = False in secrets.py to leave them alone.

Badges
------
Crest URLs come with the match data and are served from a token-free CDN that
counts against no quota. They arrive as 200x200 PNGs and pngdec can only scale
images up, so football_badges.py shrinks each one to BADGE_SIZE and keeps it
in memory. Until a badge is ready the team's initials are drawn in its place,
and badges are fetched one per pass of the main loop so the dashboard stays
responsive on the first run.

Nothing is written to flash
---------------------------
Presto drives the display from a render loop on core1. A flash write has to
lock that core out while XIP is halted, and on hardware that deadlocks the
board - the screen freezes and USB stops responding. So this script keeps all
of its state in RAM and re-derives it at startup instead. The only cost is one
call per team per boot to re-read recent results, plus one per named team to
look up its id: put numeric ids in FAVOURITE_TEAMS to avoid the latter.
"""

import gc
import time

import ntptime
import pngdec
import requests
from football_badges import BadgeCache
from picovector import ANTIALIAS_BEST, PicoVector, Polygon, Transform
from presto import Presto

# --- Configuration ----------------------------------------------------------

try:
    from secrets import FAVOURITE_TEAMS
except ImportError:
    FAVOURITE_TEAMS = []

try:
    from secrets import FOOTBALL_DATA_TOKEN
except ImportError:
    FOOTBALL_DATA_TOKEN = ""

# Hours ahead of UTC, e.g. 5.5 for Kolkata, -5 for New York.
# football-data.org only ever reports UTC.
try:
    from secrets import UTC_OFFSET
except ImportError:
    UTC_OFFSET = 0

try:
    from secrets import USE_24_HOUR
except ImportError:
    USE_24_HOUR = True

try:
    from secrets import LEDS_ENABLED
except ImportError:
    LEDS_ENABLED = True

try:
    from secrets import LED_BRIGHTNESS
except ImportError:
    LED_BRIGHTNESS = 1.0

API_HOST = "https://api.football-data.org/v4"

# The free tier allows 10 requests a minute. There is no daily cap, so the
# budget is about spacing calls out rather than rationing a daily allowance.
REQUESTS_PER_MINUTE = 10

# Never fire two calls closer together than this, whatever else is going on.
MIN_CALL_GAP_S = 7

# How far ahead to ask for fixtures. One call per team covers the recent past
# (for a final score to hold) and the next few months of upcoming matches.
FIXTURE_WINDOW_DAYS = 120
FIXTURE_LOOKBACK_DAYS = 2

# How often to re-read each team's fixture list (seconds). One call per team.
SCHEDULE_REFRESH_S = 6 * 60 * 60

# How long to wait before trying again after a failed fixture refresh, so a
# network or API problem cannot retry its way into the rate limit.
SCHEDULE_RETRY_S = 10 * 60

# Start watching a match this long before kick off, and give up this long
# after it, in case the competition never reports a finished status.
PRE_KICKOFF_LEAD_S = 5 * 60
MATCH_MAX_WINDOW_S = 3 * 60 * 60 + 30 * 60

# How long a final score stays on screen after the match ends.
RESULT_HOLD_S = 24 * 60 * 60

# Bounds for the live polling interval, kept comfortably inside the rate
# limit: one call a minute per team while a match is on.
MIN_LIVE_INTERVAL_S = 60
MAX_LIVE_INTERVAL_S = 15 * 60

# Leave this off when the dashboard runs on its own. If a host has the USB
# serial port open but is not reading it, the CDC transmit buffer fills and
# print() blocks forever - which looks exactly like the board having hung.
# Turn it on only while watching the output over a serial connection.
DEBUG = False

# The twelve competitions the free tier covers, big leagues first so a team
# name is usually found in the first call or two.
FREE_COMPETITIONS = ("PL", "PD", "SA", "BL1", "FL1", "CL",
                     "DED", "PPL", "ELC", "BSA", "EC", "WC")

# Match status values, from the football-data.org v4 documentation.
IN_PLAY = ("IN_PLAY", "PAUSED")
FINISHED = ("FINISHED", "AWARDED")
ABANDONED = ("POSTPONED", "SUSPENDED", "CANCELLED")

DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# --- Display setup ----------------------------------------------------------

presto = Presto(full_res=True, ambient_light=False)
display = presto.display
WIDTH, HEIGHT = display.get_bounds()

vector = PicoVector(display)
vector.set_antialiasing(ANTIALIAS_BEST)
vector.set_transform(Transform())
vector.set_font("Roboto-Medium.af", 24)
vector.set_font_letter_spacing(100)
vector.set_font_word_spacing(100)

PANEL_RGB = (26, 30, 40)

BACKGROUND = display.create_pen(12, 14, 20)
PANEL = display.create_pen(*PANEL_RGB)
PANEL_EDGE = display.create_pen(44, 50, 64)
TEXT = display.create_pen(234, 238, 246)
MUTED = display.create_pen(126, 136, 156)
ACCENT = display.create_pen(0, 214, 126)
LIVE_RED = display.create_pen(240, 72, 84)
AMBER = display.create_pen(252, 182, 74)
HOME = display.create_pen(96, 166, 255)

MARGIN = 16

# Badges are shrunk to this box once and cached. Everything is laid out around
# it, so one size covers both the score panel and the fixture rows.
BADGE_SIZE = 36
BADGE_GAP = 12

# --- Ambient LEDs -----------------------------------------------------------
#
# Presto has seven LEDs around the edge of the screen. Their order comes from
# the driver's ambient-light sampling points, which run anticlockwise from the
# bottom right:
#
#     4 --- 3 --- 2        left  = 4, 5, 6      right = 0, 1, 2
#     |           |        top centre = 3
#     5           1
#     |           |
#     6 --------- 0
#
# so a match can be shown with the home side lit on the left and the away side
# on the right, with the top centre blending the two.
LED_COUNT = 7
LED_LEFT = (4, 5, 6)
LED_RIGHT = (0, 1, 2)
LED_MIDDLE = 3

# How long each club colour stays up while no football is on.
LED_IDLE_CYCLE_S = 15 * 60

# A change fades the LEDs down, swaps colour, then fades back up. Each half
# takes this long.
LED_FADE_S = 1.2

# How often the fade is stepped. Anything smoother than this is wasted on
# LEDs behind a diffuser.
LED_TICK_MS = 80


def log(*args):
    if DEBUG:
        print(*args)


png = pngdec.PNG(display)
badges = BadgeCache(BADGE_SIZE, PANEL_RGB, log=log)


# --- Time helpers -----------------------------------------------------------

# MicroPython on the RP2 counts from 2000-01-01; the API uses Unix time.
EPOCH_SHIFT = 946684800 if time.gmtime(0)[0] == 2000 else 0

# Seconds to add to Unix time to get the user's wall clock. football-data.org
# reports UTC only, so this conversion is ours to do.
tz_offset = int(UTC_OFFSET * 3600)


def now_unix():
    return time.time() + EPOCH_SHIFT


# time.time() is whole seconds on MicroPython, too coarse to fade with, so the
# LEDs run off the millisecond tick counter instead. CPython has no ticks_ms,
# hence the fallback for the desktop tests.
try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a civil date (Howard Hinnant's algorithm)."""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def parse_iso(s):
    """Split '2026-09-21T15:00:00Z' into parts plus its UTC offset."""
    year = int(s[0:4])
    month = int(s[5:7])
    day = int(s[8:10])
    hour = int(s[11:13])
    minute = int(s[14:16])
    offset = 0
    tail = s[19:]
    if tail and tail[0] in "+-":
        offset = int(tail[1:3]) * 3600 + int(tail[4:6]) * 60
        if tail[0] == "-":
            offset = -offset
    return year, month, day, hour, minute, offset


# Roboto-Medium.af carries only the 95 printable ASCII glyphs, so accented
# names would draw with gaps. Folding them down keeps "Atletico Madrid" and
# "Campeonato Brasileiro Serie A" readable.
ACCENTS = {
    "à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a", "æ": "ae",
    "ç": "c", "è": "e", "é": "e", "ê": "e", "ë": "e",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "ñ": "n", "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o", "ø": "o",
    "ù": "u", "ú": "u", "û": "u", "ü": "u", "ý": "y", "ÿ": "y",
    "ß": "ss", "þ": "th", "ð": "d",
    "ā": "a", "ă": "a", "ą": "a", "ć": "c", "č": "c", "ď": "d", "đ": "d",
    "ē": "e", "ė": "e", "ę": "e", "ě": "e", "ğ": "g", "ħ": "h",
    "ī": "i", "į": "i", "ı": "i", "ł": "l", "ń": "n", "ň": "n", "ņ": "n",
    "ō": "o", "ő": "o", "œ": "oe", "ř": "r", "ś": "s", "š": "s", "ş": "s",
    "ť": "t", "ţ": "t", "ū": "u", "ů": "u", "ű": "u", "ų": "u",
    "ź": "z", "ż": "z", "ž": "z", "ș": "s", "ț": "t",
}


def to_ascii(text):
    """Fold a name onto the glyphs the font actually has."""
    out = ""
    for ch in text:
        if " " <= ch <= "~":
            out += ch
            continue
        lower = ch.lower()
        replacement = ACCENTS.get(lower)
        if replacement is None:
            continue                     # drop what we cannot represent
        out += replacement.upper() if ch != lower else replacement
    return out.strip() or "?"


def iso_to_unix(s):
    """Unix timestamp for an ISO date. football-data always sends UTC."""
    year, month, day, hour, minute, offset = parse_iso(s)
    return (days_from_civil(year, month, day) * 86400
            + hour * 3600 + minute * 60 - offset)


def civil_from_unix(ts):
    """Unix timestamp to (y, m, d, hour, minute, weekday index)."""
    days, rem = divmod(int(ts), 86400)
    hour, rem = divmod(rem, 3600)
    minute = rem // 60
    # Walk back from 1970 using the inverse of days_from_civil.
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    y += 1 if m <= 2 else 0
    return y, m, d, hour, minute, (days + 4) % 7


def format_time(hour, minute):
    if USE_24_HOUR:
        return "{:02d}:{:02d}".format(hour, minute)
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    return "{}:{:02d}{}".format(display_hour, minute, suffix)


def format_duration(seconds):
    """A short 'time until kick off' string."""
    if seconds < 0:
        seconds = 0
    minutes = seconds // 60
    if minutes < 60:
        return "in {}m".format(minutes)
    hours = minutes // 60
    if hours < 24:
        return "in {}h {}m".format(hours, minutes % 60)
    return "in {}d {}h".format(hours // 24, hours % 24)


# --- Persistent state -------------------------------------------------------

# Everything here lives in RAM only. Writing to flash while Presto's display
# loop is running on core1 has to lock that core out to halt XIP, and on
# hardware that deadlocks the board - screen frozen, USB dead. Nothing below
# needs to survive a reboot anyway: fixtures, results and badges are all
# re-fetched at startup, well inside the rate limit.
state = {
    "team_ids": {},       # secrets name -> resolved football-data team id
    "results": {},        # match id -> finished match, held for 24h
    "crests": {},         # team id -> crest URL, learned from the match data
    "recent_calls": [],   # timestamps of recent requests, for rate limiting
}


def calls_in_last_minute():
    now = now_unix()
    state["recent_calls"] = [t for t in state["recent_calls"] if now - t < 60]
    return len(state["recent_calls"])


def wait_for_slot(max_wait=75):
    """Block until a request slot frees up.

    Looking a team name up can mean walking several competitions in a row,
    which would otherwise trip the per-minute limit part way through and
    report the team as missing. Waiting is fine here: it only happens during
    startup, and only when names are used instead of ids.
    """
    waited = 0
    while calls_in_last_minute() >= REQUESTS_PER_MINUTE and waited < max_wait:
        time.sleep(3)
        waited += 3
    return waited


# --- API client -------------------------------------------------------------

class ApiError(Exception):
    pass


def api_get(endpoint, params=None):
    """GET an endpoint, spacing requests out to respect the rate limit."""
    if not FOOTBALL_DATA_TOKEN:
        raise ApiError("No FOOTBALL_DATA_TOKEN in secrets.py")

    # The free tier allows REQUESTS_PER_MINUTE; refuse rather than get a 429.
    if calls_in_last_minute() >= REQUESTS_PER_MINUTE:
        raise ApiError("Rate limit reached, backing off")

    url = API_HOST + endpoint
    if params:
        url += "?" + "&".join("{}={}".format(k, v) for k, v in params.items())

    log("GET", url)
    response = None
    try:
        response = requests.get(url, headers={"X-Auth-Token": FOOTBALL_DATA_TOKEN})
        state["recent_calls"].append(now_unix())

        if response.status_code == 429:
            raise ApiError("Rate limited by the server")
        if response.status_code in (401, 403):
            raise ApiError("Token rejected (HTTP {})".format(response.status_code))
        if response.status_code != 200:
            raise ApiError("HTTP {}".format(response.status_code))

        payload = response.json()
    finally:
        if response is not None:
            response.close()

    # Errors come back as a message field rather than a 200 body.
    message = payload.get("message") or payload.get("error")
    if message and "matches" not in payload:
        raise ApiError(str(message))

    return payload


def slim_fixture(entry, favourite_id):
    """Map a football-data match onto the fields we draw.

    Times arrive as UTC only.
    """
    home = entry["homeTeam"]
    away = entry["awayTeam"]
    competition = entry.get("competition") or {}
    score = entry.get("score") or {}
    full_time = score.get("fullTime") or {}
    half_time = score.get("halfTime") or {}

    ts = iso_to_unix(entry["utcDate"])

    # Remember the crests so badges need no extra lookup.
    for team in (home, away):
        if team.get("crest") and team.get("id"):
            state["crests"][team["id"]] = team["crest"]

    # The fixture list already carries everything the single-match endpoint
    # returns on the free tier, so keeping these here means opening a match's
    # detail view costs no extra request.
    referees = entry.get("referees") or []
    referee = ""
    for official in referees:
        if official.get("type") in (None, "REFEREE"):
            referee = to_ascii(official.get("name") or "")
            break

    return {
        "id": entry["id"],
        "ts": ts,
        "date": entry["utcDate"],
        "status": entry.get("status", "SCHEDULED"),
        "elapsed": entry.get("minute"),
        "league": to_ascii(competition.get("name", "")),
        "home": to_ascii(home.get("shortName") or home.get("name") or "?"),
        "home_id": home.get("id"),
        "away": to_ascii(away.get("shortName") or away.get("name") or "?"),
        "away_id": away.get("id"),
        "gh": full_time.get("home"),
        "ga": full_time.get("away"),
        "gh_ht": half_time.get("home"),
        "ga_ht": half_time.get("away"),
        "winner": score.get("winner"),
        "matchday": entry.get("matchday"),
        "stage": entry.get("stage"),
        "referee": referee,
        "fav": favourite_id,
        "finished_at": None,
    }


# Club-type words to ignore when matching a name, so "Manchester City"
# matches "Manchester City FC" and "Real Madrid" matches "Real Madrid CF".
CLUB_WORDS = ("fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "cd",
              "rc", "sv", "bv", "vfb", "vfl", "tsg", "fsv", "club", "calcio")


def normalise_team(name):
    """Lower-case, strip punctuation, and drop club-type words."""
    cleaned = ""
    for ch in name.lower():
        cleaned += ch if (ch.isalpha() or ch.isdigit()) else " "
    words = [w for w in cleaned.split() if w and w not in CLUB_WORDS]
    return " ".join(words)


def resolve_team(name):
    """Turn a team name into a football-data id, once per run."""
    if isinstance(name, int):
        return name
    key = str(name)
    if key in state["team_ids"]:
        return state["team_ids"][key]

    # The free tier has no search endpoint, so look through the teams of the
    # competitions it does cover. Exact matches win; a suffix-insensitive
    # match is the fallback so "Manchester City" finds "Manchester City FC".
    wanted = key.lower()
    wanted_norm = normalise_team(key)
    fallback = None

    for code in FREE_COMPETITIONS:
        try:
            wait_for_slot()
            payload = api_get("/competitions/{}/teams".format(code))
        except ApiError as e:
            log("Team lookup in", code, "failed:", e)
            continue

        for team in payload.get("teams", []):
            names = [team.get("name"), team.get("shortName"), team.get("tla")]
            if any(n and n.lower() == wanted for n in names):
                return _remember_team(key, team)
            if fallback is None and any(n and normalise_team(n) == wanted_norm
                                        for n in names if n):
                fallback = team

        if fallback is not None:
            return _remember_team(key, fallback)

    raise ApiError("No team matching '{}'".format(key))


def _remember_team(key, team):
    state["team_ids"][key] = team["id"]
    if team.get("crest"):
        state["crests"][team["id"]] = team["crest"]
    log("Resolved", key, "->", team["id"], team.get("name"))
    return team["id"]


def date_string(ts):
    year, month, day = civil_from_unix(ts)[0:3]
    return "{:04d}-{:02d}-{:02d}".format(year, month, day)


def fetch_team_matches(team_id):
    """One call per team: recent results, anything live, and what is next.

    The free tier has no "next N" shortcut, so we ask for a date window
    instead. Starting a couple of days back means a just-finished match is
    still in the response, which is what keeps a final score on screen.
    """
    now = now_unix()
    payload = api_get("/teams/{}/matches".format(team_id), {
        "dateFrom": date_string(now - FIXTURE_LOOKBACK_DAYS * 86400),
        "dateTo": date_string(now + FIXTURE_WINDOW_DAYS * 86400),
    })
    matches = payload.get("matches", [])
    fixtures = [slim_fixture(m, team_id) for m in matches]
    del payload, matches
    gc.collect()
    fixtures.sort(key=lambda f: f["ts"])
    return fixtures


# --- Fixture bookkeeping ----------------------------------------------------

schedule = {}            # team id -> list of upcoming fixtures
live_fixtures = {}       # fixture id -> fixture currently being watched
next_schedule_fetch = 0
next_live_poll = 0
status_message = ""
dirty = True             # set whenever something on screen has changed

# --- Views ------------------------------------------------------------------

view = "dashboard"       # or "detail"
detail_fixture = None
detail_scroll = 0
detail_height = 0        # how tall the current detail content is
detail_opened_at = 0
detail_h2h = {}          # match id -> [past meetings], or [] for none, or
                         # missing entirely while it has not been fetched
h2h_wanted = None        # match id whose head to head still needs fetching

# Rectangles the dashboard drew, as (x, y, w, h, fixture), so a touch can be
# matched back to the card under it.
touch_targets = []


def all_known_fixtures():
    """Every upcoming fixture across all teams, de-duplicated and sorted."""
    merged = {}
    for fixtures in schedule.values():
        for fixture in fixtures:
            merged[fixture["id"]] = fixture
    # Live data is fresher than the cached schedule.
    for fixture_id, fixture in live_fixtures.items():
        merged[fixture_id] = fixture
    result = list(merged.values())
    result.sort(key=lambda f: f["ts"])
    return result


def held_results():
    """Finished matches still inside their 24 hour display window."""
    now = now_unix()
    stale = [fid for fid, f in state["results"].items()
             if now - (f.get("finished_at") or f["ts"]) > RESULT_HOLD_S]
    for fid in stale:
        del state["results"][fid]
    results = list(state["results"].values())
    results.sort(key=lambda f: f["ts"])
    return results


def remember_result(fixture):
    fixture = dict(fixture)
    fixture["finished_at"] = now_unix()
    state["results"][str(fixture["id"])] = fixture
    log("Stored final score:", fixture["home"], fixture["gh"], "-", fixture["ga"], fixture["away"])


def visible_fixtures():
    """The fixtures the screen can actually show: a featured one, then five.

    draw_screen picks from the same set, so this is what decides which badges
    are worth fetching.
    """
    now = now_unix()
    featured = held_results()[-1:] + [f for f in live_fixtures.values()
                                      if f["status"] in IN_PLAY]
    upcoming = [f for f in all_known_fixtures()
                if f["ts"] > now - PRE_KICKOFF_LEAD_S
                and f["status"] not in FINISHED
                and f["status"] not in ABANDONED]
    return featured + upcoming[:5]


def queue_badges():
    """Ask the cache for the badges that will actually be drawn.

    Only what is on screen: a season's worth of opponents would be twenty-odd
    downloads and conversions for fixtures months away, and each conversion
    costs real time and memory. The crest URL travels with the match data, so
    no extra lookup is needed.
    """
    wanted = []
    for fixture in visible_fixtures():
        for team_id in (fixture["home_id"], fixture["away_id"]):
            url = state["crests"].get(team_id)
            if team_id and url:
                wanted.append((team_id, url))
    badges.request(wanted)


def is_watchable(fixture, now):
    """True if this fixture could be in progress right now."""
    if fixture["status"] in FINISHED or fixture["status"] in ABANDONED:
        return False
    return (fixture["ts"] - PRE_KICKOFF_LEAD_S) <= now <= (fixture["ts"] + MATCH_MAX_WINDOW_S)


def featured_fixture():
    """The match to show in the big panel, if there is one."""
    in_play = [f for f in live_fixtures.values() if f["status"] in IN_PLAY]
    if in_play:
        in_play.sort(key=lambda f: f["ts"])
        return in_play[-1], True
    recent = held_results()
    if recent:
        return recent[-1], False
    return None, False


# --- Ambient LEDs -----------------------------------------------------------

led_colours = [(0, 0, 0)] * LED_COUNT    # what is lit, at full brightness
led_level = 0.0                          # 0 = dark, 1 = full
led_fading_out = False
led_written = None                       # last values sent, to skip no-op writes
led_last_tick = 0
led_next_cycle = 0
led_cycle_index = 0


def team_colour(team_id, index=0):
    """A club's colour, or None until its crest has been converted."""
    palette = badges.colours(team_id)
    if not palette:
        return None
    return palette[index % len(palette)]


def mix(first, second):
    return ((first[0] + second[0]) // 2,
            (first[1] + second[1]) // 2,
            (first[2] + second[2]) // 2)


def split_leds(home, away):
    """Home down the left, away down the right, blended across the top."""
    if home is None and away is None:
        return [(0, 0, 0)] * LED_COUNT
    if home is None:
        home = away
    if away is None:
        away = home

    colours = [(0, 0, 0)] * LED_COUNT
    for i in LED_LEFT:
        colours[i] = home
    for i in LED_RIGHT:
        colours[i] = away
    colours[LED_MIDDLE] = mix(home, away)
    return colours


def idle_palette():
    """Every colour we could cycle through while no football is on."""
    colours = []
    for team in FAVOURITE_TEAMS:
        team_id = state["team_ids"].get(str(team), team if isinstance(team, int) else None)
        if team_id is None:
            continue
        for colour in badges.colours(team_id):
            if colour not in colours:
                colours.append(colour)
    return colours


def desired_leds():
    """The colours the current state calls for, before fading is applied."""
    featured, is_live = featured_fixture()

    if featured is not None:
        home = team_colour(featured["home_id"])
        away = team_colour(featured["away_id"])
        if is_live:
            return split_leds(home, away)

        # Finished: the winner's colour all the way round. A draw has no
        # winner, so it keeps the two-sided split.
        goals_home, goals_away = featured["gh"], featured["ga"]
        if goals_home is not None and goals_away is not None and goals_home != goals_away:
            winner = home if goals_home > goals_away else away
            if winner is not None:
                return [winner] * LED_COUNT
        return split_leds(home, away)

    palette = idle_palette()
    if not palette:
        return [(0, 0, 0)] * LED_COUNT
    return [palette[led_cycle_index % len(palette)]] * LED_COUNT


def write_leds():
    global led_written
    level = led_level * LED_BRIGHTNESS
    values = [(int(r * level), int(g * level), int(b * level))
              for r, g, b in led_colours]
    if values == led_written:
        return                           # nothing moved, save the calls
    for i in range(LED_COUNT):
        presto.set_led_rgb(i, *values[i])
    led_written = values


def update_leds():
    """Step the fade. Called often; does nothing between ticks."""
    global led_level, led_fading_out, led_colours, led_last_tick
    global led_next_cycle, led_cycle_index

    if not LEDS_ENABLED:
        return

    now = ticks_ms()
    elapsed = ticks_diff(now, led_last_tick)
    if elapsed < LED_TICK_MS:
        return
    led_last_tick = now
    step = min(elapsed, 500) / (LED_FADE_S * 1000.0)

    # Advance the idle cycle when its turn is up. The first tick only starts
    # the clock, so the cycle opens on the first colour rather than the second.
    unix_now = now_unix()
    if led_next_cycle == 0:
        led_next_cycle = unix_now + LED_IDLE_CYCLE_S
    elif unix_now >= led_next_cycle:
        led_next_cycle = unix_now + LED_IDLE_CYCLE_S
        led_cycle_index += 1

    wanted = desired_leds()

    if led_fading_out:
        led_level -= step
        if led_level <= 0.0:
            led_level = 0.0
            led_colours = wanted
            led_fading_out = False
    elif wanted != led_colours and led_level > 0.0:
        led_fading_out = True            # phase out before showing the change
    else:
        led_colours = wanted
        led_level = min(1.0, led_level + step)

    write_leds()


# --- Touch ------------------------------------------------------------------

# A press has to move less than this to count as a tap rather than a drag.
TAP_SLOP = 12

# Leave a detail view on its own after this long with no touching, so the
# board goes back to being an ambient dashboard.
DETAIL_TIMEOUT_S = 90

touch_down = False
touch_start = (0, 0)
touch_last_y = 0
touch_moved = False


def fixture_at(x, y):
    for left, top, width, height, fixture in touch_targets:
        if left <= x <= left + width and top <= y <= top + height:
            return fixture
    return None


def open_detail(fixture):
    global view, detail_fixture, detail_scroll, detail_opened_at, dirty
    global h2h_wanted
    view = "detail"
    detail_fixture = fixture
    detail_scroll = 0
    detail_opened_at = now_unix()
    dirty = True
    if fixture["id"] not in detail_h2h:
        h2h_wanted = fixture["id"]
    log("Opened detail for", fixture["home"], "v", fixture["away"])


def close_detail():
    global view, detail_fixture, detail_scroll, dirty, h2h_wanted
    view = "dashboard"
    detail_fixture = None
    detail_scroll = 0
    h2h_wanted = None
    dirty = True


def handle_touch():
    """Poll the screen and turn presses into taps and scrolls."""
    global touch_down, touch_start, touch_last_y, touch_moved
    global detail_scroll, dirty, detail_opened_at

    presto.touch_poll()
    x, y, touched = presto.touch_a

    if touched and not touch_down:
        touch_down = True
        touch_start = (x, y)
        touch_last_y = y
        touch_moved = False
        if view == "detail":
            detail_opened_at = now_unix()
        return

    if touched and touch_down:
        if abs(y - touch_start[1]) > TAP_SLOP or abs(x - touch_start[0]) > TAP_SLOP:
            touch_moved = True
        # Drag to scroll, but only where there is more than a screenful.
        if view == "detail" and touch_moved:
            overflow = detail_height - (HEIGHT - DETAIL_TOP)
            if overflow > 0:
                detail_scroll = min(max(0, detail_scroll + (touch_last_y - y)), overflow)
                dirty = True
            detail_opened_at = now_unix()
        touch_last_y = y
        return

    if not touched and touch_down:
        touch_down = False
        if touch_moved:
            return                       # that was a scroll, not a tap
        if view == "detail":
            if touch_start[1] <= DETAIL_TOP:
                close_detail()
        else:
            fixture = fixture_at(*touch_start)
            if fixture is not None:
                open_detail(fixture)


def fetch_h2h(match_id):
    """Past meetings for a match. One call, only when a detail view asks."""
    payload = api_get("/matches/{}/head2head".format(match_id), {"limit": 10})
    meetings = []
    for entry in payload.get("matches", []):
        score = entry.get("score") or {}
        full_time = score.get("fullTime") or {}
        if full_time.get("home") is None:
            continue
        year, month, day, _h, _m, _wd = civil_from_unix(iso_to_unix(entry["utcDate"]))
        winner = score.get("winner") or ""
        meetings.append({
            "when": "{} {} {}".format(day, MONTH_NAMES[month - 1], str(year)[2:]),
            "home": to_ascii(entry["homeTeam"].get("tla")
                             or entry["homeTeam"].get("shortName") or "?"),
            "away": to_ascii(entry["awayTeam"].get("tla")
                             or entry["awayTeam"].get("shortName") or "?"),
            "home_id": entry["homeTeam"].get("id"),
            "gh": full_time.get("home"),
            "ga": full_time.get("away"),
            # Normalised so h2h_record does not have to know the API's wording.
            "winner": "DRAW" if winner == "DRAW" else (
                "HOME" if winner == "HOME_TEAM" else "AWAY"),
        })
    return meetings


def poll_h2h():
    """Fetch the head to head a detail view is waiting on, if any."""
    global h2h_wanted, dirty
    if h2h_wanted is None:
        return
    if calls_in_last_minute() >= REQUESTS_PER_MINUTE - 1:
        return                           # leave room for the live polling
    match_id = h2h_wanted
    h2h_wanted = None
    try:
        detail_h2h[match_id] = fetch_h2h(match_id)
    except (ApiError, OSError, ValueError, KeyError) as e:
        log("Head to head failed:", e)
        detail_h2h[match_id] = []        # show "no meetings" rather than hang
    dirty = True


# --- Call pacing ------------------------------------------------------------

# The free tier limits requests per minute rather than per day, so pacing is
# about spacing calls out. One call per team per minute while a match is on
# sits well inside the limit even with several teams.

def live_interval():
    """How long to wait before the next live poll."""
    teams = max(1, len(schedule))
    # Leave room for the occasional fixture refresh alongside the live polls.
    per_minute_budget = max(1, REQUESTS_PER_MINUTE - 2)
    interval = max(MIN_LIVE_INTERVAL_S,
                   int(60 * teams / per_minute_budget),
                   MIN_CALL_GAP_S * teams)
    return min(MAX_LIVE_INTERVAL_S, interval)


# --- Update logic -----------------------------------------------------------

def refresh_schedule(force=False):
    global next_schedule_fetch, status_message, dirty

    now = now_unix()
    if not force and now < next_schedule_fetch:
        return

    fetched_any = False
    for team in FAVOURITE_TEAMS:
        try:
            team_id = resolve_team(team)
            fixtures = fetch_team_matches(team_id)
            # The same call carries recent results, so pick those up here
            # rather than spending another request on them.
            for fixture in fixtures:
                if (fixture["status"] in FINISHED
                        and now - fixture["ts"] < RESULT_HOLD_S
                        and str(fixture["id"]) not in state["results"]):
                    fixture = dict(fixture)
                    fixture["finished_at"] = fixture["ts"]
                    state["results"][str(fixture["id"])] = fixture
            schedule[team_id] = [f for f in fixtures if f["status"] not in FINISHED]
            fetched_any = True
        except (ApiError, OSError, ValueError, KeyError) as e:
            status_message = "Fixtures: {}".format(e)
            log("Schedule fetch failed for", team, "-", e)

    # Always set the next attempt, successful or not - otherwise a failure
    # would be retried on every pass of the main loop.
    next_schedule_fetch = now + (SCHEDULE_REFRESH_S if fetched_any else SCHEDULE_RETRY_S)

    if fetched_any:
        status_message = ""
        dirty = True
        # A fixture we were watching may have vanished (postponed, or simply
        # played) - drop anything that is no longer live.
        for fixture_id in list(live_fixtures):
            if not is_watchable(live_fixtures[fixture_id], now):
                del live_fixtures[fixture_id]
        queue_badges()


def poll_live():
    """Fetch every in-progress match in a single call."""
    global next_live_poll, status_message, dirty

    now = now_unix()
    if now < next_live_poll:
        return

    watching = [f for f in all_known_fixtures() if is_watchable(f, now)]
    if not watching:
        next_live_poll = now + 60
        return

    # Which teams have something worth watching right now. football-data has
    # no "several matches by id" endpoint, so this is one call per team - but
    # only for teams actually playing.
    teams_playing = []
    for fixture in watching:
        team_id = fixture.get("fav")
        if team_id is not None and team_id not in teams_playing:
            teams_playing.append(team_id)

    updated = []
    failed = False
    for team_id in teams_playing:
        try:
            updated.extend(fetch_team_matches(team_id))
        except (ApiError, OSError, ValueError, KeyError) as e:
            status_message = "Live: {}".format(e)
            log("Live poll failed:", e)
            failed = True
            break

    if failed:
        next_live_poll = now + MAX_LIVE_INTERVAL_S
        return

    status_message = ""
    dirty = True
    needs_reschedule = False
    watched_ids = [f["id"] for f in watching]

    for fixture in updated:
        if fixture["id"] not in watched_ids:
            continue
        # Carry the favourite team through from the cached copy.
        for known in watching:
            if known["id"] == fixture["id"]:
                fixture["fav"] = known["fav"]
                break
        live_fixtures[fixture["id"]] = fixture

        if fixture["status"] in FINISHED:
            remember_result(fixture)
            del live_fixtures[fixture["id"]]
            needs_reschedule = True
        elif fixture["status"] in ABANDONED:
            del live_fixtures[fixture["id"]]
            needs_reschedule = True

    next_live_poll = now + live_interval()
    log("Next live poll in", next_live_poll - now, "s")
    queue_badges()

    if needs_reschedule:
        # The finished match has dropped out of the upcoming list; pull a
        # fresh one so a fifth fixture appears behind it.
        refresh_schedule(force=True)


# --- Drawing ----------------------------------------------------------------

def rounded_rect(x, y, w, h, radius, pen):
    display.set_pen(pen)
    shape = Polygon()
    shape.rectangle(x, y, w, h, (radius, radius, radius, radius))
    vector.draw(shape)


def fit_text(text, max_width):
    """Trim text with an ellipsis until it fits."""
    if vector.measure_text(text)[2] <= max_width:
        return text
    while text and vector.measure_text(text + ".")[2] > max_width:
        text = text[:-1]
    return text.rstrip() + "."


def draw_right(text, right_x, y):
    width = vector.measure_text(text)[2]
    vector.text(text, int(right_x - width), y)


def initials_for(name):
    words = [w for w in name.split(" ") if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:3].upper() if name else "?"


def draw_badge(team_id, name, x, y):
    """The team's badge, or its initials while the badge is still coming."""
    blob = badges.buffer(team_id)
    if blob:
        width, height = badges.size_of(team_id)
        try:
            png.open_RAM(blob)
            png.decode(x + (BADGE_SIZE - width) // 2, y + (BADGE_SIZE - height) // 2)
            return
        except (OSError, RuntimeError, ValueError) as e:
            log("Badge would not draw:", team_id, "-", e)
            badges.forget(team_id)

    radius = BADGE_SIZE // 2
    display.set_pen(PANEL_EDGE)
    disc = Polygon()
    disc.circle(x + radius, y + radius, radius)
    vector.draw(disc)

    text = initials_for(name)
    vector.set_font_size(15)
    display.set_pen(MUTED)
    width = vector.measure_text(text)[2]
    vector.text(text, int(x + radius - width / 2), y + radius + 5)


def fixture_label(fixture):
    """Opponent name and whether our team is at home."""
    favourite = fixture.get("fav")
    if fixture["away_id"] == favourite:
        return fixture["home"], False
    return fixture["away"], True


def fixture_when(fixture):
    """'Sat 27 Sep' and '15:00' in the user's timezone."""
    year, month, day, hour, minute, weekday = civil_from_unix(fixture["ts"] + tz_offset)

    local_now = now_unix() + tz_offset
    ny, nm, nd = civil_from_unix(local_now)[0:3]
    today = days_from_civil(ny, nm, nd)
    delta = days_from_civil(year, month, day) - today

    if delta == 0:
        date_text = "Today"
    elif delta == 1:
        date_text = "Tomorrow"
    else:
        date_text = "{} {} {}".format(DAY_NAMES[weekday], day, MONTH_NAMES[month - 1])
    return date_text, format_time(hour, minute)


def draw_header():
    local_now = now_unix() + tz_offset
    _, _, _, hour, minute, _ = civil_from_unix(local_now)

    vector.set_font_size(20)
    display.set_pen(MUTED)
    vector.text("FOOTBALL", MARGIN, 30)

    display.set_pen(TEXT)
    draw_right(format_time(hour, minute), WIDTH - MARGIN, 30)

    # A quiet note while badges are still arriving, so a first run looks like
    # progress rather than a fault.
    vector.set_font_size(14)
    display.set_pen(MUTED)
    if badges.pending:
        draw_right("{} badges loading".format(len(badges.pending)),
                   WIDTH - MARGIN, 48)

    if status_message:
        display.set_pen(AMBER)
        vector.text(fit_text(status_message, WIDTH - 2 * MARGIN), MARGIN, 48)


def draw_score_line(team_id, name, goals, y, is_favourite, dim):
    badge_x = MARGIN + 14
    text_x = badge_x + BADGE_SIZE + BADGE_GAP
    draw_badge(team_id, name, badge_x, y - 26)

    vector.set_font_size(28)
    display.set_pen(ACCENT if is_favourite else (MUTED if dim else TEXT))
    vector.text(fit_text(name, WIDTH - MARGIN - 60 - text_x), text_x, y)

    vector.set_font_size(32)
    display.set_pen(TEXT)
    draw_right("-" if goals is None else str(goals), WIDTH - MARGIN - 14, y)


def draw_featured(fixture, is_live, y, height):
    rounded_rect(MARGIN, y, WIDTH - 2 * MARGIN, height, 12, PANEL)
    touch_targets.append((MARGIN, y, WIDTH - 2 * MARGIN, height, fixture))

    vector.set_font_size(16)
    display.set_pen(MUTED)
    vector.text(fit_text(fixture["league"].upper(), WIDTH - 2 * MARGIN - 130),
                MARGIN + 14, y + 28)

    # Status badge: a live minute, or the full time marker.
    if is_live:
        elapsed = fixture.get("elapsed")
        if fixture["status"] == "PAUSED":
            badge = "HALF TIME"
        elif elapsed is not None:
            badge = "{}'".format(elapsed)
        else:
            badge = "LIVE"
        badge_pen = LIVE_RED
    else:
        badge = "FULL TIME"
        badge_pen = MUTED

    display.set_pen(badge_pen)
    if is_live:
        dot = Polygon()
        dot.circle(WIDTH - MARGIN - 20, y + 22, 5)
        vector.draw(dot)
    draw_right(badge, WIDTH - MARGIN - (32 if is_live else 14), y + 28)

    favourite = fixture.get("fav")
    draw_score_line(fixture["home_id"], fixture["home"], fixture["gh"], y + 78,
                    fixture["home_id"] == favourite, not is_live)
    draw_score_line(fixture["away_id"], fixture["away"], fixture["ga"], y + 124,
                    fixture["away_id"] == favourite, not is_live)


def draw_fixture_row(fixture, y, height, emphasise):
    # The two text lines are placed proportionally so a row reads the same
    # whether we are showing four of them beneath a score or five on their own.
    inner = height - 8
    line_one = y + int(inner * 0.45)
    line_two = y + int(inner * 0.82)

    rounded_rect(MARGIN, y, WIDTH - 2 * MARGIN, inner, 10, PANEL)
    touch_targets.append((MARGIN, y, WIDTH - 2 * MARGIN, inner, fixture))

    opponent, at_home = fixture_label(fixture)
    opponent_id = fixture["home_id"] if not at_home else fixture["away_id"]
    date_text, time_text = fixture_when(fixture)

    # Home/away marker down the left edge.
    display.set_pen(HOME if at_home else PANEL_EDGE)
    marker = Polygon()
    marker.rectangle(MARGIN, y + 8, 4, inner - 16, (2, 2, 2, 2))
    vector.draw(marker)

    draw_badge(opponent_id, opponent, MARGIN + 14, y + (inner - BADGE_SIZE) // 2)
    text_x = MARGIN + 14 + BADGE_SIZE + BADGE_GAP
    text_room = WIDTH - MARGIN - 90 - text_x

    vector.set_font_size(22 if emphasise else 20)
    display.set_pen(TEXT)
    label = "{} {}".format("vs" if at_home else "at", opponent)
    vector.text(fit_text(label, text_room), text_x, line_one)

    vector.set_font_size(22)
    display.set_pen(ACCENT if emphasise else TEXT)
    draw_right(time_text, WIDTH - MARGIN - 14, line_one)

    vector.set_font_size(15)
    display.set_pen(MUTED)
    vector.text(fit_text(fixture["league"], text_room), text_x, line_two)
    draw_right(date_text, WIDTH - MARGIN - 14, line_two)


def draw_empty(y):
    vector.set_font_size(20)
    display.set_pen(MUTED)
    message = "No fixtures found" if FAVOURITE_TEAMS else "Set FAVOURITE_TEAMS in secrets.py"
    vector.text(fit_text(message, WIDTH - 2 * MARGIN), MARGIN, y)


def draw_screen():
    display.set_pen(BACKGROUND)
    display.clear()
    del touch_targets[:]

    draw_header()

    now = now_unix()
    featured, is_live = featured_fixture()

    upcoming = [f for f in all_known_fixtures()
                if f["ts"] > now - PRE_KICKOFF_LEAD_S
                and f["status"] not in FINISHED
                and f["status"] not in ABANDONED]
    if featured is not None:
        upcoming = [f for f in upcoming if f["id"] != featured["id"]]

    if featured is not None:
        draw_featured(featured, is_live, 58, 142)
        list_top = 210
        slots = 4
    else:
        list_top = 70
        slots = 5

    vector.set_font_size(14)
    display.set_pen(MUTED)
    vector.text("NEXT UP" if featured is not None else "NEXT 5 MATCHES",
                MARGIN, list_top + 4)

    rows = upcoming[:slots]
    if not rows:
        draw_empty(list_top + 40)
    else:
        row_top = list_top + 16
        row_height = (HEIGHT - row_top - 12) // slots
        for index, fixture in enumerate(rows):
            emphasise = index == 0 and featured is None
            draw_fixture_row(fixture, row_top + index * row_height, row_height, emphasise)

        # A countdown on the next match, when nothing is being featured.
        if featured is None and rows:
            vector.set_font_size(14)
            display.set_pen(ACCENT)
            draw_right(format_duration(rows[0]["ts"] - now), WIDTH - MARGIN, list_top + 4)

    presto.update()


# --- Detail view ------------------------------------------------------------
#
# Touching a card opens the match behind it. The free tier does not carry
# goal, card or substitution events - only the half time and full time
# scores - so a played match is drawn as a timeline of the two halves with a
# marker per goal on the scoring side. Everything else that is available
# (head to head, the referee, the matchday) fills out the rest.

DETAIL_TOP = 48                  # below the back bar
GOAL_DOT = 7
TIMELINE_GAP = 26


def draw_back_bar():
    """The bar along the top of a detail view, with its touch target."""
    display.set_pen(PANEL)
    bar = Polygon()
    bar.rectangle(0, 0, WIDTH, DETAIL_TOP)
    vector.draw(bar)

    vector.set_font_size(22)
    display.set_pen(ACCENT)
    vector.text("<", MARGIN, 32)
    vector.set_font_size(19)
    display.set_pen(TEXT)
    vector.text("Back", MARGIN + 18, 31)

    local_now = now_unix() + tz_offset
    _, _, _, hour, minute, _ = civil_from_unix(local_now)
    vector.set_font_size(17)
    display.set_pen(MUTED)
    draw_right(format_time(hour, minute), WIDTH - MARGIN, 31)


def match_state(fixture):
    """'upcoming', 'live' or 'played'."""
    if fixture["status"] in IN_PLAY:
        return "live"
    if fixture["status"] in FINISHED:
        return "played"
    return "upcoming"


def draw_detail_header(fixture, y):
    """Crests, names, and either the score or the kick off time."""
    height = 132
    rounded_rect(MARGIN, y, WIDTH - 2 * MARGIN, height, 12, PANEL)

    state_name = match_state(fixture)
    home_x, away_x = MARGIN + 14, WIDTH - MARGIN - 14 - BADGE_SIZE
    draw_badge(fixture["home_id"], fixture["home"], home_x, y + 16)
    draw_badge(fixture["away_id"], fixture["away"], away_x, y + 16)

    # Score in the middle, or a plain "v" before kick off.
    if state_name == "upcoming":
        vector.set_font_size(30)
        display.set_pen(MUTED)
        middle = "v"
    else:
        vector.set_font_size(40)
        display.set_pen(TEXT)
        middle = "{} - {}".format(
            "-" if fixture["gh"] is None else fixture["gh"],
            "-" if fixture["ga"] is None else fixture["ga"])
    width = vector.measure_text(middle)[2]
    vector.text(middle, int(WIDTH / 2 - width / 2), y + 48)

    # Names under their crest, each kept on its own half.
    vector.set_font_size(17)
    display.set_pen(TEXT)
    room = WIDTH // 2 - MARGIN - 24
    vector.text(fit_text(fixture["home"], room), MARGIN + 14, y + 78)
    away_text = fit_text(fixture["away"], room)
    draw_right(away_text, WIDTH - MARGIN - 14, y + 78)

    # Status line.
    if state_name == "live":
        elapsed = fixture.get("elapsed")
        if fixture["status"] == "PAUSED":
            label, pen = "HALF TIME", LIVE_RED
        elif elapsed is not None:
            label, pen = "{}'".format(elapsed), LIVE_RED
        else:
            label, pen = "LIVE", LIVE_RED
    elif state_name == "played":
        label, pen = "FULL TIME", MUTED
    else:
        date_text, time_text = fixture_when(fixture)
        label, pen = "{}  {}".format(date_text, time_text), ACCENT

    vector.set_font_size(16)
    display.set_pen(pen)
    width = vector.measure_text(label)[2]
    vector.text(label, int(WIDTH / 2 - width / 2), y + 108)

    return height


def goal_split(fixture):
    """Goals per half for each side, as ((h1, a1), (h2, a2)).

    Only the half time and full time scores are published, so we know how
    many goals each side scored in each half but not when or by whom.
    """
    goals_home = fixture.get("gh") or 0
    goals_away = fixture.get("ga") or 0
    half_home = fixture.get("gh_ht")
    half_away = fixture.get("ga_ht")
    if half_home is None or half_away is None:
        # Half time not published: attribute everything to one band.
        return (goals_home, goals_away), (0, 0)
    second_home = max(0, goals_home - half_home)
    second_away = max(0, goals_away - half_away)
    return (half_home, half_away), (second_home, second_away)


def draw_goal_dots(count, y, on_left, colour):
    """A dot per goal, marching outwards from the centre line."""
    centre = WIDTH // 2
    display.set_pen(colour)
    for i in range(min(count, 6)):
        offset = 26 + i * (GOAL_DOT * 2 + 8)
        x = centre - offset if on_left else centre + offset
        dot = Polygon()
        dot.circle(x, y, GOAL_DOT)
        vector.draw(dot)
    if count > 6:
        vector.set_font_size(14)
        display.set_pen(colour)
        extra = "+{}".format(count - 6)
        offset = 26 + 6 * (GOAL_DOT * 2 + 8)
        x = centre - offset if on_left else centre + offset
        vector.text(extra, int(x - 8), y + 5)


def team_pen(team_id, fallback):
    """A pen in the club's colour, falling back when no crest has arrived."""
    colour = team_colour(team_id)
    if colour is None:
        return fallback
    return display.create_pen(*colour)


def draw_timeline(fixture, y):
    """The two halves down a centre line, a dot per goal on the scoring side."""
    centre = WIDTH // 2
    first, second = goal_split(fixture)
    state_name = match_state(fixture)
    home_pen = team_pen(fixture["home_id"], HOME)
    away_pen = team_pen(fixture["away_id"], LIVE_RED)

    def marker(text, at_y, pen=MUTED, size=14):
        vector.set_font_size(size)
        display.set_pen(BACKGROUND)
        width = vector.measure_text(text)[2]
        # Punch a gap in the line so the label reads cleanly.
        block = Polygon()
        block.rectangle(int(centre - width / 2) - 8, at_y - 14, int(width) + 16, 20)
        vector.draw(block)
        display.set_pen(pen)
        vector.text(text, int(centre - width / 2), at_y)

    rows = []
    rows.append(("label", "KICK OFF", MUTED))
    rows.append(("goals", first, None))
    if state_name == "played" or fixture.get("gh_ht") is not None:
        half_text = "HALF TIME  {} - {}".format(
            fixture.get("gh_ht") if fixture.get("gh_ht") is not None else 0,
            fixture.get("ga_ht") if fixture.get("ga_ht") is not None else 0)
        rows.append(("label", half_text, TEXT))
        rows.append(("goals", second, None))
    if state_name == "played":
        rows.append(("label", "FULL TIME  {} - {}".format(fixture.get("gh") or 0,
                                                          fixture.get("ga") or 0), TEXT))
    else:
        elapsed = fixture.get("elapsed")
        rows.append(("label", "{}'".format(elapsed) if elapsed else "IN PLAY", LIVE_RED))

    # Work out the height first so the line can be drawn behind everything.
    height = 0
    for kind, value, _pen in rows:
        height += TIMELINE_GAP if kind == "label" else max(
            TIMELINE_GAP, (GOAL_DOT * 2 + 10) if (value[0] or value[1]) else TIMELINE_GAP)

    display.set_pen(PANEL_EDGE)
    line = Polygon()
    line.rectangle(centre - 1, y, 3, height)
    vector.draw(line)

    at = y
    nothing_yet = True
    for kind, value, pen in rows:
        if kind == "label":
            at += TIMELINE_GAP
            marker(value, at, pen)
        else:
            step = max(TIMELINE_GAP, (GOAL_DOT * 2 + 10) if (value[0] or value[1]) else TIMELINE_GAP)
            at += step
            if value[0]:
                draw_goal_dots(value[0], at - 6, True, home_pen)
                nothing_yet = False
            if value[1]:
                draw_goal_dots(value[1], at - 6, False, away_pen)
                nothing_yet = False

    if nothing_yet:
        vector.set_font_size(15)
        display.set_pen(MUTED)
        text = "No goals yet" if state_name == "live" else "Goalless"
        width = vector.measure_text(text)[2]
        # Off to one side so it does not sit on the line.
        vector.text(text, int(centre - width / 2), y + height // 2 + 5)

    return height


def h2h_side(fixture):
    """Whose point of view the head to head is shown from.

    Your own club, when it is playing - "won 3" is only meaningful if you
    know who won. Otherwise the home side.
    """
    favourite = fixture.get("fav")
    if favourite in (fixture["home_id"], fixture["away_id"]):
        return favourite
    return fixture["home_id"]


def h2h_record(fixture, meetings):
    """Wins, draws and losses from h2h_side's point of view."""
    side = h2h_side(fixture)
    won = drawn = lost = 0
    for meeting in meetings:
        if meeting["winner"] == "DRAW":
            drawn += 1
        elif (meeting["winner"] == "HOME") == (meeting["home_id"] == side):
            won += 1
        else:
            lost += 1
    return won, drawn, lost


def draw_h2h(fixture, y):
    """Head to head: the record, then the recent meetings."""
    entry = detail_h2h.get(fixture["id"])
    start = y

    side = h2h_side(fixture)
    side_name = fixture["home"] if side == fixture["home_id"] else fixture["away"]

    vector.set_font_size(14)
    display.set_pen(MUTED)
    vector.text("HEAD TO HEAD - {}".format(side_name.upper()), MARGIN, y + 12)

    if entry is None:
        display.set_pen(MUTED)
        vector.set_font_size(15)
        vector.text("Loading...", MARGIN, y + 40)
        return 56
    if not entry:
        display.set_pen(MUTED)
        vector.set_font_size(15)
        vector.text("No previous meetings", MARGIN, y + 40)
        return 56

    draw_right("last {}".format(len(entry)), WIDTH - MARGIN, y + 12)

    won, drawn, lost = h2h_record(fixture, entry)
    y += 28
    rounded_rect(MARGIN, y, WIDTH - 2 * MARGIN, 58, 10, PANEL)
    third = (WIDTH - 2 * MARGIN) // 3
    for index, (value, label, pen) in enumerate(
            ((won, "won", ACCENT), (drawn, "drawn", MUTED), (lost, "lost", LIVE_RED))):
        cx = MARGIN + third * index + third // 2
        vector.set_font_size(26)
        display.set_pen(pen)
        width = vector.measure_text(str(value))[2]
        vector.text(str(value), int(cx - width / 2), y + 30)
        vector.set_font_size(13)
        display.set_pen(MUTED)
        width = vector.measure_text(label)[2]
        vector.text(label, int(cx - width / 2), y + 48)
    y += 70

    vector.set_font_size(15)
    for meeting in entry[:6]:
        display.set_pen(MUTED)
        vector.text(meeting["when"], MARGIN, y)
        display.set_pen(TEXT)
        line = "{} {}-{} {}".format(meeting["home"], meeting["gh"],
                                    meeting["ga"], meeting["away"])
        draw_right(fit_text(line, WIDTH - 2 * MARGIN - 110), WIDTH - MARGIN, y)
        y += 24

    return y - start


def draw_detail_footer(fixture, y):
    """Competition, matchday and referee - whatever we happen to know."""
    bits = []
    if fixture.get("league"):
        bits.append(fixture["league"])
    if fixture.get("matchday"):
        bits.append("Matchday {}".format(fixture["matchday"]))
    lines = []
    if bits:
        lines.append(" - ".join(bits))
    if fixture.get("referee"):
        lines.append("Referee: {}".format(fixture["referee"]))

    vector.set_font_size(14)
    display.set_pen(MUTED)
    for line in lines:
        vector.text(fit_text(line, WIDTH - 2 * MARGIN), MARGIN, y + 12)
        y += 20
    return len(lines) * 20 + 8


def draw_detail():
    """The whole detail screen, honouring the scroll offset."""
    fixture = detail_fixture
    display.set_pen(BACKGROUND)
    display.clear()

    if fixture is None:
        draw_back_bar()
        presto.update()
        return 0

    y = DETAIL_TOP + 10 - detail_scroll
    y += draw_detail_header(fixture, y) + 16

    if match_state(fixture) == "upcoming":
        y += draw_detail_footer(fixture, y) + 6
        y += draw_h2h(fixture, y) + 8
    else:
        y += draw_timeline(fixture, y) + 22
        y += draw_detail_footer(fixture, y) + 6
        y += draw_h2h(fixture, y) + 8

    content_height = y + detail_scroll - DETAIL_TOP

    # The bar is drawn last so scrolled content slides underneath it.
    draw_back_bar()
    presto.update()
    return content_height


def show_message(text):
    display.set_pen(BACKGROUND)
    display.clear()
    vector.set_font_size(22)
    display.set_pen(TEXT)
    vector.text(text, MARGIN, HEIGHT // 2, max_width=WIDTH - 2 * MARGIN)
    presto.update()


# --- Startup ----------------------------------------------------------------

show_message("Connecting...")

try:
    presto.connect()
except (ValueError, ImportError, RuntimeError) as e:
    while True:
        show_message("WiFi failed:\n{}".format(e))
        time.sleep(5)

show_message("Getting the time...")
for _attempt in range(5):
    try:
        ntptime.settime()
        break
    except OSError:
        time.sleep(2)
else:
    while True:
        show_message("Could not get the time.\nCheck secrets.py and try again.")
        time.sleep(5)

show_message("Loading fixtures...")
refresh_schedule(force=True)
queue_badges()
gc.collect()

# --- Main loop --------------------------------------------------------------

# Everything on screen is minute-resolution - the clock, the kick off times and
# the countdown - so there is no reason to redraw more often than that, or when
# something actually changes.
last_minute = None

while True:
    try:
        refresh_schedule()
        poll_live()

        # One badge per pass, so the first run fills in gradually instead of
        # blocking on a season's worth of opponents.
        if badges.process_one():
            dirty = True

        handle_touch()
        poll_h2h()

        # Drop back to the dashboard if a detail view is left untouched.
        if view == "detail" and now_unix() - detail_opened_at > DETAIL_TIMEOUT_S:
            close_detail()

        minute = int((now_unix() + tz_offset) // 60)
        if dirty or minute != last_minute:
            last_minute = minute
            dirty = False
            if view == "detail":
                detail_height = draw_detail()
            else:
                draw_screen()
            gc.collect()

        # Stepped far more often than the screen so the fades look smooth;
        # it rate-limits itself internally.
        update_leds()

    except (ApiError, OSError, ValueError, KeyError) as e:
        # Never let a transient failure stop the clock.
        status_message = str(e)
        dirty = True
        log("Loop error:", e)
        time.sleep(5)

    # Short enough for the LED fades, and everything else in the loop is a
    # cheap "is it time yet?" check.
    time.sleep(0.05)
