"""task-60: sleep-stage chart colors (recovery.js stagesChart()).

Deep/REM/Awake reuse pal()'s accent/good/bad; Light normally reuses warn but
the ember and canyon themes define --accent and --warn as the SAME hex, so
Deep and Light rendered identically (owner-reported bug — two orange stages).
recovery.js's stageLightColor() detects that collision at render time and
derives Light by rotating the resolved warn hue to blue, clamping
lightness/saturation into a legible band (see task-60 report for the
validate_palette.js output that band was chosen against).

The derivation is JS-side HSL math, not a CSS value, so this test can't
assert it purely from panel.css. Instead it parses every theme block's
--accent/--good/--warn/--bad hex values (the same source-of-truth idiom as
test_bridge.py's LOGGABLE/ALLOWED parsing) and mirrors stageLightColor()'s
algorithm in Python, then asserts the four resulting stage colors are
pairwise distinct in every theme — the regression this fix is for."""
import re
from colorsys import hls_to_rgb
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "panel.css").read_text()

# Same theme-block shape test_bridge.py relies on for LOGGABLE/ALLOWED:
# find each selector's declaration block and pull out the vars we need.
THEME_BLOCK_RE = re.compile(
    r"(:root(?:,\s*\[data-theme=\"(\w+)\"\])?|\[data-theme=\"(\w+)\"\])\s*\{([^}]*)\}", re.S
)
VAR_RE = re.compile(r"--(accent|good|warn|bad):\s*(#[0-9a-fA-F]{6})")
DARK_RE = re.compile(r"color-scheme:\s*dark")


def parsed_themes():
    """[data-theme] blocks that define the four stage-source vars, in file order."""
    themes = []
    for m in THEME_BLOCK_RE.finditer(CSS):
        name = m.group(2) or m.group(3) or "paper"
        body = m.group(4)
        colors = dict(VAR_RE.findall(body))
        if {"accent", "good", "warn", "bad"} <= colors.keys():
            themes.append((name, colors, bool(DARK_RE.search(body))))
    return themes


def hex_to_hsl(hexcolor):
    n = int(hexcolor[1:], 16)
    r, g, b = ((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    d = mx - mn
    s = 0.0 if not d else d / (1 - abs(2 * l - 1))
    return s, l


def hsl_to_hex(h, s, l):
    r, g, b = hls_to_rgb(h / 360.0, l, s)
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in (r, g, b))


def stage_light_color(accent, warn, is_dark):
    """Mirror of recovery.js's stageLightColor(P)."""
    if warn.lower() != accent.lower():
        return warn
    s, _l = hex_to_hsl(warn)
    return hsl_to_hex(210, max(s, 0.70), 0.58 if is_dark else 0.45)


THEMES = parsed_themes()


def test_panel_css_theme_parse_sanity():
    # sanity check on the regex itself, mirroring test_labs_read's
    # "the block parse actually found tables" check
    names = {name for name, _, _ in THEMES}
    assert {"paper", "ember", "canyon", "alpine", "cyber"} <= names
    assert len(THEMES) >= 12


@pytest.mark.parametrize("name,colors,is_dark", THEMES, ids=[t[0] for t in THEMES])
def test_stage_colors_pairwise_distinct(name, colors, is_dark):
    deep, rem, bad = colors["accent"], colors["good"], colors["bad"]
    light = stage_light_color(colors["accent"], colors["warn"], is_dark)
    stages = {"Deep": deep.lower(), "REM": rem.lower(), "Light": light.lower(), "Awake": bad.lower()}
    seen = {}
    for label, hexval in stages.items():
        assert hexval not in seen, (
            f"{name}: {label} and {seen.get(hexval)} resolve to the same color {hexval}"
        )
        seen[hexval] = label


def test_ember_and_canyon_actually_exercise_the_collision_path():
    """Confirms this test suite is exercising the real bug: without the
    stageLightColor() derivation (i.e. Light = plain warn), ember and canyon
    are exactly the case that used to collide."""
    by_name = {name: colors for name, colors, _ in THEMES}
    for name in ("ember", "canyon"):
        colors = by_name[name]
        assert colors["accent"].lower() == colors["warn"].lower(), (
            f"{name} no longer collides accent/warn — this test may be stale"
        )
