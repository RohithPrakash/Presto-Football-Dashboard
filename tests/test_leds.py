"""Check the ambient LED state machine and the crest colour extraction."""
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
clock = {"t": NOW, "ms": 0}
fs.now_unix = lambda: clock["t"]
fs.ticks_ms = lambda: clock["ms"]
fs.ticks_diff = lambda a, b: a - b

CITY, RIVAL = 65, 61
CITY_BLUE = (108, 171, 221)
RIVAL_RED = (200, 16, 46)

# --- a fake badge cache that serves known palettes ---------------------------
PALETTES = {CITY: [CITY_BLUE, (1, 50, 101), (250, 192, 32)],
            RIVAL: [RIVAL_RED, (20, 20, 20)]}
fs.badges.colours = lambda team_id: PALETTES.get(team_id, [])

# what the LEDs were last told to be
written = {}
fs.presto.set_led_rgb = lambda i, r, g, b: written.__setitem__(i, (r, g, b))


def reset_leds(level=1.0):
    fs.led_colours = [(0, 0, 0)] * fs.LED_COUNT
    fs.led_level = level
    fs.led_fading_out = False
    fs.led_written = None
    fs.led_last_tick = 0
    fs.led_next_cycle = 0
    fs.led_cycle_index = 0
    fs.live_fixtures.clear()
    fs.schedule.clear()
    fs.state["results"] = {}
    fs.state["team_ids"] = {}
    written.clear()


def fixture(fid, ts, status, gh=None, ga=None, home=CITY, away=RIVAL):
    return {"id": fid, "ts": ts, "date": "", "status": status, "elapsed": None,
            "league": "Premier League", "home": "Home", "home_id": home,
            "away": "Away", "away_id": away, "gh": gh, "ga": ga,
            "fav": CITY, "finished_at": None}


# --- the LED map matches the driver's physical layout ------------------------
check("seven LEDs", fs.LED_COUNT, 7)
check("left side", sorted(fs.LED_LEFT), [4, 5, 6])
check("right side", sorted(fs.LED_RIGHT), [0, 1, 2])
check("top centre", fs.LED_MIDDLE, 3)
ok("no LED is in two groups",
   len(set(fs.LED_LEFT) | set(fs.LED_RIGHT) | {fs.LED_MIDDLE}) == fs.LED_COUNT)

# --- live match: home left, away right, blended across the top ---------------
reset_leds()
fs.live_fixtures[1] = fixture(1, NOW - 600, "IN_PLAY", 1, 0)
leds = fs.desired_leds()
for i in fs.LED_LEFT:
    check(f"LED {i} shows the home colour", leds[i], CITY_BLUE)
for i in fs.LED_RIGHT:
    check(f"LED {i} shows the away colour", leds[i], RIVAL_RED)
check("top centre blends the two", leds[fs.LED_MIDDLE], fs.mix(CITY_BLUE, RIVAL_RED))

# half time is still a live match
fs.live_fixtures[1]["status"] = "PAUSED"
check("half time keeps the split", fs.desired_leds()[fs.LED_LEFT[0]], CITY_BLUE)

# --- finished: the winner takes the whole ring -------------------------------
reset_leds()
fs.state["results"] = {"1": dict(fixture(1, NOW - 7200, "FINISHED", 3, 1),
                                 finished_at=NOW - 3600)}
check("home win lights every LED blue", fs.desired_leds(), [CITY_BLUE] * 7)

fs.state["results"]["1"]["gh"], fs.state["results"]["1"]["ga"] = 0, 2
check("away win lights every LED red", fs.desired_leds(), [RIVAL_RED] * 7)

# a draw has no winner, so it keeps the two-sided split
fs.state["results"]["1"]["gh"], fs.state["results"]["1"]["ga"] = 2, 2
drawn = fs.desired_leds()
check("draw keeps home on the left", drawn[fs.LED_LEFT[0]], CITY_BLUE)
check("draw keeps away on the right", drawn[fs.LED_RIGHT[0]], RIVAL_RED)

# --- idle: cycle the favourites' colours -------------------------------------
reset_leds()
fs.FAVOURITE_TEAMS = [CITY]
fs.state["team_ids"] = {}
palette = fs.idle_palette()
check("idle palette comes from the favourite", palette, PALETTES[CITY])

seen = []
for step in range(len(palette) * 2):
    fs.led_cycle_index = step
    colours = fs.desired_leds()
    ok(f"idle lights all LEDs the same at step {step}",
       len(set(colours)) == 1, str(colours[:2]))
    seen.append(colours[0])
check("cycles through every colour", sorted(set(seen)), sorted(set(palette)))
check("wraps around", seen[0], seen[len(palette)])

# names resolve through team_ids too
reset_leds()
fs.FAVOURITE_TEAMS = ["Man City"]
fs.state["team_ids"] = {"Man City": CITY}
check("idle palette works for a named team", fs.idle_palette(), PALETTES[CITY])

# --- unknown colours degrade quietly -----------------------------------------
reset_leds()
fs.FAVOURITE_TEAMS = [999]
check("no crest yet means LEDs off", fs.desired_leds(), [(0, 0, 0)] * 7)

reset_leds()
fs.live_fixtures[1] = fixture(1, NOW - 600, "IN_PLAY", 1, 0, home=CITY, away=999)
half = fs.desired_leds()
check("missing away colour falls back to home", half[fs.LED_RIGHT[0]], CITY_BLUE)
check("known side still correct", half[fs.LED_LEFT[0]], CITY_BLUE)

# --- the fade phases out, swaps, then phases in ------------------------------
reset_leds(level=1.0)
fs.FAVOURITE_TEAMS = [CITY]
fs.led_colours = [CITY_BLUE] * 7
fs.led_cycle_index = 0

# settle at full brightness
for _ in range(40):
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
check("settles at full brightness", round(fs.led_level, 3), 1.0)
lit = written[0]
ok("LEDs actually lit", sum(lit) > 0, str(lit))
ok("brightness scaled by LED_BRIGHTNESS",
   abs(lit[0] - int(CITY_BLUE[0] * fs.LED_BRIGHTNESS)) <= 1, str(lit))

# force a change and watch it dip to black before the new colour appears
fs.led_cycle_index = 1
wanted = PALETTES[CITY][1]
levels = []
swapped_at_zero = True
for _ in range(120):
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
    levels.append(fs.led_level)
    if fs.led_colours == [wanted] * 7 and fs.led_level > 0.05:
        swapped_at_zero = False
        break
    if fs.led_colours == [wanted] * 7:
        break

ok("faded down before swapping", min(levels) <= 0.001, f"min level {min(levels):.3f}")
ok("colour swapped only at black", swapped_at_zero)
check("new colour adopted", fs.led_colours, [wanted] * 7)

# and back up again
for _ in range(60):
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
check("fades back to full", round(fs.led_level, 3), 1.0)
ok("now showing the second colour",
   abs(written[0][0] - int(wanted[0] * fs.LED_BRIGHTNESS)) <= 1, str(written[0]))

# --- a fade takes roughly LED_FADE_S each way --------------------------------
reset_leds(level=1.0)
fs.led_colours = [CITY_BLUE] * 7
fs.FAVOURITE_TEAMS = [CITY]
fs.led_cycle_index = 0
for _ in range(40):
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
start_ms = clock["ms"]
fs.led_cycle_index = 1
while fs.led_level > 0.001 and clock["ms"] - start_ms < 10000:
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
fade_s = (clock["ms"] - start_ms) / 1000.0
ok("fade out takes about LED_FADE_S",
   fs.LED_FADE_S * 0.6 <= fade_s <= fs.LED_FADE_S * 1.8, f"{fade_s:.2f}s")

# --- the idle cycle advances on schedule -------------------------------------
reset_leds(level=1.0)
fs.FAVOURITE_TEAMS = [CITY]
clock["ms"] += fs.LED_TICK_MS
fs.update_leds()
first_index = fs.led_cycle_index
check("first tick does not skip a colour", first_index, 0)

clock["t"] += fs.LED_IDLE_CYCLE_S - 5
clock["ms"] += fs.LED_TICK_MS
fs.update_leds()
check("holds before the interval is up", fs.led_cycle_index, first_index)

clock["t"] += 10
clock["ms"] += fs.LED_TICK_MS
fs.update_leds()
check("advances once the interval passes", fs.led_cycle_index, first_index + 1)

# --- LEDS_ENABLED = False leaves them alone ----------------------------------
reset_leds()
written.clear()
fs.LEDS_ENABLED = False
for _ in range(20):
    clock["ms"] += fs.LED_TICK_MS
    fs.update_leds()
check("disabled means no writes at all", written, {})
fs.LEDS_ENABLED = True

# --- write_leds skips redundant writes ---------------------------------------
reset_leds(level=1.0)
fs.led_colours = [CITY_BLUE] * 7
fs.write_leds()
written.clear()
fs.write_leds()
check("identical values are not rewritten", written, {})

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    seen_msgs = set()
    for f in fails:
        if f not in seen_msgs:
            seen_msgs.add(f)
            print("  -", f)
    sys.exit(1)
print("All LED checks passed.")
