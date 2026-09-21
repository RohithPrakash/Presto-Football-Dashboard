"""Check every scene's geometry: nothing off-screen, overflowing or overlapping."""
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
import sys
import preview          # builds the scenes and leaves them in preview.scenes
from preview import text_width

W = H = 480
MARGIN = preview.fs.MARGIN
fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name}{(': ' + detail) if detail else ''}")


for name, ops in preview.scenes:
    texts = [o for o in ops if o[0] == "text"]
    shapes = [o for o in ops if o[0] == "shape" and o[1] is not None]

    check(f"[{name}] draws something", len(texts) > 3, f"{len(texts)} strings")

    for _, t, x, y, size, _pen in texts:
        w = text_width(t, size)
        check(f"[{name}] '{t}' starts on screen", x >= 0, f"x={x}")
        check(f"[{name}] '{t}' fits horizontally", x + w <= W - 4,
              f"x={x:.0f} w={w:.0f} right={x + w:.0f}")
        check(f"[{name}] '{t}' baseline on screen", size <= y <= H,
              f"y={y}")
        check(f"[{name}] '{t}' not clipped at bottom", y <= H - 2, f"y={y}")

    for _, shape, _pen in shapes:
        if shape[0] == "rect":
            _, x, y, w, h, _r = shape
            check(f"[{name}] rect in bounds", x >= 0 and y >= 0 and x + w <= W and y + h <= H,
                  f"{x},{y} {w}x{h}")
        else:
            _, x, y, r = shape
            check(f"[{name}] circle in bounds",
                  x - r >= 0 and y - r >= 0 and x + r <= W and y + r <= H, f"{x},{y} r{r}")

    # Panels (the rounded cards) must not overlap one another.
    cards = sorted([s[1] for s in shapes if s[1][0] == "rect" and s[1][3] > 300],
                   key=lambda s: s[2])
    for a, b in zip(cards, cards[1:]):
        a_bottom = a[2] + a[4]
        check(f"[{name}] cards do not overlap", b[2] >= a_bottom - 0.01,
              f"card at y={a[2]} h={a[4]} vs next at y={b[2]}")

    # Every text must sit inside some card, or in the header strip above them.
    top_card = min((c[2] for c in cards), default=H)
    for _, t, _x, y, size, _pen in texts:
        inside = any(c[2] - size <= y <= c[2] + c[4] + 2 for c in cards)
        check(f"[{name}] '{t}' is inside a panel or the header",
              inside or y <= top_card, f"y={y}, first card at {top_card}")

    print(f"  {name}: {len(texts)} strings, {len(cards)} cards, "
          f"bottom edge {max((c[2] + c[4]) for c in cards) if cards else 0:.0f}")

# The scene-specific expectations.
by_name = dict(preview.scenes)


def strings(scene):
    return [o[1] for o in by_name[scene] if o[0] == "text"]


up = strings("Upcoming only")
check("upcoming lists five matches", sum(1 for s in up if s.startswith(("vs ", "at "))) == 5,
      str([s for s in up if s.startswith(("vs ", "at "))]))
check("upcoming has the section title", "NEXT 5 MATCHES" in up, str(up[:6]))
check("upcoming shows a countdown", any(s.startswith("in ") for s in up), str(up))

live = strings("Live match")
check("live shows the minute", "67'" in live, str(live[:8]))
check("live lists four more", sum(1 for s in live if s.startswith(("vs ", "at "))) == 4,
      str([s for s in live if s.startswith(("vs ", "at "))]))
check("live has the section title", "NEXT UP" in live)
check("live shows both scores", live.count("2") >= 1 and live.count("1") >= 1)

ht = strings("Half time")
check("half time badge", "HALF TIME" in ht, str(ht[:8]))

fin = strings("Finished (held 24h)")
check("finished badge", "FULL TIME" in fin, str(fin[:8]))
check("finished lists four more", sum(1 for s in fin if s.startswith(("vs ", "at "))) == 4,
      str([s for s in fin if s.startswith(("vs ", "at "))]))
# fit_text must actually shorten a name that cannot fit.
preview.cur["size"] = 20
long_name = "vs Borussia Monchengladbach & Co"
fitted = preview.fs.fit_text(long_name, 150)
check("fit_text truncates", len(fitted) < len(long_name) and fitted.endswith("."), fitted)
check("fit_text result fits", text_width(fitted, 20) <= 150, str(text_width(fitted, 20)))
check("fit_text leaves short text alone", preview.fs.fit_text("vs Inter", 150) == "vs Inter")

# --- badges ------------------------------------------------------------------
for name, ops in preview.scenes:
    drawn = [o for o in ops if o[0] == "badge"]
    cards = sorted([s[1] for s in ops if s[0] == "shape" and s[1] and s[1][0] == "rect"
                    and s[1][3] > 300], key=lambda s: s[2])
    if name == "Badges still loading":
        check("[loading] falls back to initials, no badges drawn", len(drawn) == 0,
              f"{len(drawn)} badges")
        labels = [o[1] for o in ops if o[0] == "text"]
        check("[loading] initials are shown",
              sum(1 for s in labels if 1 <= len(s) <= 3 and s.isupper()) >= 5,
              str([s for s in labels if 1 <= len(s) <= 3]))
        continue
    expected = 6 if name != "Upcoming only" else 5   # 2 in the panel + 4 rows
    check(f"[{name}] every slot has a badge", len(drawn) == expected,
          f"{len(drawn)} badges, expected {expected}")

    for _, tag, bx, by in drawn:
        team_id = int(tag.strip().split(":")[1])
        bw, bh = preview.BadgeCache(36, preview.fs.PANEL_RGB).size_of(team_id)
        check(f"[{name}] badge {team_id} on screen",
              bx >= 0 and by >= 0 and bx + bw <= W and by + bh <= H, f"{bx},{by} {bw}x{bh}")
        check(f"[{name}] badge {team_id} within its box", bw <= 36 and bh <= 36, f"{bw}x{bh}")
        inside = any(c[1] <= bx and c[2] <= by and bx + bw <= c[1] + c[3]
                     and by + bh <= c[2] + c[4] for c in cards)
        check(f"[{name}] badge {team_id} sits inside a card", inside, f"at {bx},{by}")

    # No badge may overlap the text that follows it.
    for _, _tag, bx, by in drawn:
        for _, t, tx, ty, _size, _pen in [o for o in ops if o[0] == "text"]:
            if by <= ty <= by + 36 and tx < bx + 36 and tx >= bx:
                fails.append(f"[{name}] text '{t}' overlaps a badge at {bx},{by}")

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    seen = set()
    for f in fails:
        if f not in seen:
            seen.add(f)
            print("  -", f)
    sys.exit(1)
print("Layout checks passed.")
