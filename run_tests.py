#!/usr/bin/env python3
"""Run the whole desktop test suite.

    uv run run_tests.py

The suites are standalone scripts rather than pytest cases: each one loads
football_scores.py with the Presto hardware stubbed out, so they need to
control import order themselves.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(HERE, "tests")

SUITES = [
    ("test_logic", "date maths, pacing, accent folding"),
    ("test_api", "API client and match lifecycle"),
    ("test_badges", "PNG decoder, checked against Pillow"),
    ("test_layout", "screen geometry for every state"),
    ("test_leds", "ambient LED states and fading"),
]


def main():
    if not os.path.isdir(os.path.join(TESTS, "badges")):
        print("Fetching the crest corpus first...", flush=True)
        subprocess.run([sys.executable, os.path.join(TESTS, "fetch_badges.py")],
                       check=False)
        print()

    failed = []
    for name, description in SUITES:
        print("=" * 68, flush=True)
        print("%s  -  %s" % (name, description), flush=True)
        print("=" * 68, flush=True)
        result = subprocess.run([sys.executable, os.path.join(TESTS, name + ".py")],
                                check=False)
        if result.returncode != 0:
            failed.append(name)
        print(flush=True)

    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("All %d suites passed." % len(SUITES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
