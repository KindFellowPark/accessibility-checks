"""contrast.py: the one copy of the WCAG contrast arithmetic.

Every check that needs a contrast ratio imports it from here, so the formula cannot drift
into several private copies that disagree.

FLOOR is 4.5:1, the WCAG 2.x AA minimum for body text (success criterion 1.4.3). The house
rule applies it to all text, including large text, where WCAG would allow 3:1.

A color given as rgba() over a solid background is blended to the solid color a reader
actually sees, then checked like any other. A pairing over a photo or a gradient has no
single background color, so it cannot be checked from the source; it is returned as
"skipped" and the callers treat a skipped pairing as a failure, never as a pass.

Standard library only.
"""
import re

FLOOR = 4.5


# WCAG relative luminance (WCAG 2.x definition).
def _linear(channel_255):
    c = channel_255 / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def ratio(fg_hex, bg_hex):
    l1, l2 = luminance(fg_hex), luminance(bg_hex)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def is_solid_hex(value):
    return isinstance(value, str) and bool(re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(value)))


def _norm(hex_color):
    return "#" + str(hex_color).lstrip("#").upper()


_RGBA_RE = re.compile(
    r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*"
    r"(0?\.\d+|[01](?:\.0+)?)\s*\)"
)


def parse_rgba(value):
    """'rgba(31,58,95,0.85)' -> (31, 58, 95, 0.85). None for anything that is not rgba()."""
    m = _RGBA_RE.fullmatch(str(value).strip())
    if not m:
        return None
    r, g, b, a = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    if not (r <= 255 and g <= 255 and b <= 255):
        return None
    return (r, g, b, a)


def composite_over(value, bg_hex):
    """The solid color a reader sees when `value` is drawn over the solid `bg_hex`.

    A solid hex passes through. An rgba() color is blended channel by channel
    (a*fg + (1-a)*bg). Anything else, or a background that is not solid, returns None:
    the pairing cannot be resolved from the source.
    """
    if not is_solid_hex(bg_hex):
        return None
    if is_solid_hex(value):
        return _norm(value)
    rgba = parse_rgba(value)
    if rgba is None:
        return None
    r, g, b, a = rgba
    h = str(bg_hex).lstrip("#")
    br, bgc, bb = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#{:02X}{:02X}{:02X}".format(
        round(a * r + (1 - a) * br),
        round(a * g + (1 - a) * bgc),
        round(a * b + (1 - a) * bb),
    )


def check_contrast(pairings, floor=FLOOR):
    """pairings: iterable of (label, fg, bg).

    Returns (failures, skipped). failures: (label, effective_fg, bg, ratio) below the floor.
    skipped: (label, fg, bg) pairings that cannot be resolved from the source.
    """
    failures, skipped = [], []
    for label, fg, bg in pairings:
        eff = composite_over(fg, bg)
        if eff is None:
            skipped.append((label, fg, bg))
            continue
        r = ratio(eff, bg)
        if r < floor:
            failures.append((label, eff, _norm(bg), round(r, 2)))
    return failures, skipped
