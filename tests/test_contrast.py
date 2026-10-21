"""checks/contrast.py against values anyone can confirm with a WCAG contrast calculator."""
import pytest

from checks import contrast


def test_black_on_white_is_21():
    assert contrast.ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_same_color_is_1():
    assert contrast.ratio("#1F3A5F", "#1F3A5F") == pytest.approx(1.0)


def test_order_does_not_matter():
    assert contrast.ratio("#1F3A5F", "#FFFFFF") == pytest.approx(contrast.ratio("#FFFFFF", "#1F3A5F"))


def test_brand_pairs():
    assert round(contrast.ratio("#1F3A5F", "#FFFFFF"), 2) == 11.48   # navy on white
    assert round(contrast.ratio("#4A5568", "#FFFFFF"), 2) == 7.53    # slate on white
    assert round(contrast.ratio("#B7791F", "#FFFFFF"), 2) == 3.64    # gold on white: too low for text


def test_check_contrast_reports_failures_and_unresolvable_pairs():
    failures, skipped = contrast.check_contrast([
        ("body", "#1A202C", "#FFFFFF"),
        ("gold text", "#B7791F", "#FFFFFF"),
        ("caption on photo", "#FFFFFF", "url(photo.jpg)"),
    ])
    assert [f[0] for f in failures] == ["gold text"]
    assert [s[0] for s in skipped] == ["caption on photo"]


def test_rgba_is_blended_over_its_background():
    # Half-transparent navy over white is the midpoint color, not navy.
    assert contrast.composite_over("rgba(31,58,95,0.5)", "#FFFFFF") == "#8F9CAF"
    failures, _ = contrast.check_contrast([("faded", "rgba(31,58,95,0.5)", "#FFFFFF")])
    assert failures and failures[0][1] == "#8F9CAF"


def test_floor_is_the_wcag_aa_body_text_value():
    assert contrast.FLOOR == 4.5
