# Boot straight into the football dashboard.
#
# This replaces Pimoroni's example launcher. The launcher creates its own
# Presto instance at boot; the dashboard would then create a second one, and
# two live Presto objects re-initialise the same display driver, DMA and PSRAM
# framebuffers on top of each other.
#
# The original launcher is saved as main_launcher_backup.py. To restore it:
#   mpremote connect /dev/ttyACM0 fs cp :main_launcher_backup.py :main.py

import os
import sys
import time

# Grace window. Once the dashboard is running it owns the display and the
# network, and getting a REPL back can mean power-cycling the board. Waiting
# here first means there is always a few seconds after a reset in which the
# board can be reached over USB - drop a file called 'noboot' on the
# filesystem during this window and the dashboard will not start.
time.sleep(8)

if "noboot" in os.listdir("/"):
    print("noboot present - staying at the REPL")
else:
    try:
        import football_scores  # noqa: F401 - importing it runs the dashboard
    except Exception as e:  # noqa: BLE001 - last resort: leave a trace on flash
        try:
            with open("/boot_error.txt", "w") as f:
                sys.print_exception(e, f)
        except Exception:  # noqa: BLE001
            pass
        raise
