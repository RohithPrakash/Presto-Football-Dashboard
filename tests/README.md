# Tests

These run on desktop CPython with the Presto hardware stubbed out, so the
logic can be exercised without a board attached.

```bash
uv run ../run_tests.py     # or, from the repo root: uv run run_tests.py
```

The runner fetches a corpus of real club crests on first use, then executes
every suite. To run one on its own:

```bash
uv run tests/test_badges.py
```

| File | Covers |
| --- | --- |
| `harness.py` | Stubs `presto`, `picovector`, `pngdec`, `requests`, `secrets`, then loads `football_scores.py` up to its main loop. |
| `test_logic.py` | Date maths round-tripped against CPython across 3000 random dates, ISO parsing, formatting, accent folding, rate-limit pacing. |
| `test_api.py` | The football-data client and a full match lifecycle (upcoming → live → half time → full time → 24h hold → expiry) against mocked responses, plus error handling. |
| `test_badges.py` | The PNG decoder compared against Pillow on real crests, every colour type and bit depth, malformed input, and the encoder round-tripping. |
| `test_layout.py` | Screen geometry for all five states: nothing off-screen, overflowing, or overlapping. |
| `test_leds.py` | The LED map against the driver's physical layout, the home/away split, winner-takes-all versus a draw, idle cycling, missing-crest fallback, and the fade state machine. |
| `test_touch.py` | Tap versus drag, card hit testing, scroll clamping at both ends, the idle timeout, goal splitting across both halves, and head-to-head parsing and perspective. |
| `preview.py` | Renders each state to `layout.html` and to the SVGs in `docs/`. |
| `fetch_badges.py` | Downloads the crest corpus into `tests/badges/` (gitignored). |

## Note on the badge comparison

`test_badges.py` checks the downscaler against Pillow. It does not assert an
absolute error threshold: the gap between our whole-pixel box filter and
Pillow's fractional-area one grows as the source image gets smaller, so any
fixed number would be a size-dependent guess. Instead it asserts that across
the corpus the output is clearly closer to Pillow's `BOX` than to `NEAREST`,
which is what proves real averaging is happening.
