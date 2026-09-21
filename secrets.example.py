# Copy this file to secrets.py and fill it in. secrets.py is gitignored.

WIFI_SSID = "your_ssid"
WIFI_PASSWORD = "your_password"

# Free token from https://www.football-data.org/client/register
FOOTBALL_DATA_TOKEN = ""

# Teams to follow. Either football-data.org numeric ids, or names which are
# looked up at startup (a name costs one or two extra API calls per boot).
# Names are matched loosely: "Manchester City", "Manchester City FC",
# "Man City" and "MCI" all resolve to the same club.
FAVOURITE_TEAMS = ["Manchester City FC"]

# Hours ahead of UTC, e.g. 5.5 for Kolkata, -5 for New York, 1 for Paris.
# football-data.org reports UTC only, so this conversion happens on the
# device - it does NOT follow daylight saving. Adjust when your clocks change.
UTC_OFFSET = 0

# Set to False for am/pm kick off times.
USE_24_HOUR = True

# The seven LEDs around the screen follow the match: team colours during a
# game, the winner's colour afterwards, and a slow cycle of your clubs'
# colours when there is no football on.
LEDS_ENABLED = True
LED_BRIGHTNESS = 1.0        # 0.0 to 1.0
