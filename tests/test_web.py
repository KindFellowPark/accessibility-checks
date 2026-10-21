"""Fixtures for checks/web.py. Each fixture is written to a temp file and audited; the
assertions pin where each check fails, warns, or stays silent. Every defect fixture has a
clean twin that must not fire. Run with: python3 -m pytest
"""
import tempfile
import unittest
from pathlib import Path

from checks import web as g

DOC_OPEN = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<title>fixture</title></head><body>')
DOC_CLOSE = '</body></html>'


def audit(body):
    """Wrap `body` in a minimal, otherwise-clean document (lang set, no
    outline:none) unless the fragment already declares its own <html>."""
    html = body if "<html" in body else DOC_OPEN + body + DOC_CLOSE
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
        fh.write(html)
        path = fh.name
    try:
        return g.audit_html(path)
    finally:
        Path(path).unlink()


# ---- alt text and contrast ------------------------------------------------

class AltCheck(unittest.TestCase):
    def test_missing_alt_fails(self):
        f = audit('<img src="x.png">')
        self.assertEqual(f["alt_missing"], [1])
        self.assertTrue(g._has_fail(f))

    def test_empty_alt_passes(self):
        f = audit('<img src="x.png" alt="">')
        self.assertEqual(f["alt_missing"], [])
        self.assertFalse(g._has_fail(f))

    def test_populated_alt_passes(self):
        f = audit('<img src="x.png" alt="a chart">')
        self.assertEqual(f["alt_missing"], [])


class ContrastCheck(unittest.TestCase):
    MANIFEST_OK = ('<script type="application/json" id="wcag-contrast-manifest">'
                   '{"text_pairings":[{"label":"body","fg":"#1A1A1A","bg":"#FFFFFF"}]}'
                   '</script>')
    MANIFEST_FAIL = ('<script type="application/json" id="wcag-contrast-manifest">'
                      '{"text_pairings":[{"label":"body","fg":"#CCCCCC","bg":"#FFFFFF"}]}'
                      '</script>')
    MANIFEST_BAD_JSON = ('<script type="application/json" id="wcag-contrast-manifest">'
                          '{not valid json</script>')

    def test_valid_manifest_clears_floor(self):
        f = audit(self.MANIFEST_OK)
        self.assertEqual(f["contrast_failures"], [])
        self.assertTrue(f["manifest_present"])
        self.assertFalse(g._has_fail(f))

    def test_failing_pairing_fails(self):
        f = audit(self.MANIFEST_FAIL)
        self.assertEqual(len(f["contrast_failures"]), 1)
        self.assertTrue(g._has_fail(f))

    def test_malformed_manifest_reports_error(self):
        f = audit(self.MANIFEST_BAD_JSON)
        self.assertIsNotNone(f["manifest_error"])
        self.assertTrue(g._has_fail(f))

    def test_absent_manifest_produces_note_not_failure(self):
        f = audit('<p>no manifest here</p>')
        self.assertFalse(f["manifest_present"])
        self.assertFalse(g._has_fail(f))


# ---- positive tabindex --------------------------------------------

class TabindexCheck(unittest.TestCase):
    def test_positive_tabindex_fails(self):
        f = audit('<div tabindex="3">x</div>')
        self.assertEqual(f["positive_tabindex"], [(1, "div", 3)])
        self.assertTrue(g._has_fail(f))

    def test_tabindex_zero_passes(self):
        f = audit('<div tabindex="0">x</div>')
        self.assertEqual(f["positive_tabindex"], [])

    def test_tabindex_negative_one_passes(self):
        f = audit('<div tabindex="-1">x</div>')
        self.assertEqual(f["positive_tabindex"], [])
        self.assertFalse(g._has_fail(f))


# ---- aria-hidden on focusable -------------------------------------

class AriaHiddenFocusableCheck(unittest.TestCase):
    def test_aria_hidden_on_button_fails(self):
        f = audit('<button aria-hidden="true">x</button>')
        self.assertEqual(f["aria_hidden_focusable"], [(1, "button")])
        self.assertTrue(g._has_fail(f))

    def test_aria_hidden_on_tabindex_zero_fails(self):
        f = audit('<div tabindex="0" aria-hidden="true">x</div>')
        self.assertEqual(len(f["aria_hidden_focusable"]), 1)

    def test_aria_hidden_on_non_focusable_passes(self):
        f = audit('<span aria-hidden="true">x</span>')
        self.assertEqual(f["aria_hidden_focusable"], [])
        self.assertFalse(g._has_fail(f))

    def test_aria_hidden_false_on_button_passes(self):
        f = audit('<button aria-hidden="false">x</button>')
        self.assertEqual(f["aria_hidden_focusable"], [])


# ---- missing lang --------------------------------------------------

class LangCheck(unittest.TestCase):
    def test_missing_lang_fails(self):
        html = '<!DOCTYPE html><html><head></head><body>x</body></html>'
        f = audit(html)
        self.assertTrue(f["missing_lang"])
        self.assertTrue(g._has_fail(f))

    def test_empty_lang_fails(self):
        html = '<!DOCTYPE html><html lang=""><head></head><body>x</body></html>'
        f = audit(html)
        self.assertTrue(f["missing_lang"])

    def test_lang_en_passes(self):
        html = '<!DOCTYPE html><html lang="en"><head></head><body>x</body></html>'
        f = audit(html)
        self.assertFalse(f["missing_lang"])

    def test_no_html_tag_not_flagged(self):
        # A bare fragment with no <html> is wrapped by whatever publishes it, and
        # that wrapper owns the language. Not a defect.
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write('<p>fragment only</p>')
            path = fh.name
        try:
            f = g.audit_html(path)
        finally:
            Path(path).unlink()
        self.assertFalse(f["missing_lang"])


# ---- outline:none without :focus-visible --------------------------

class OutlineFocusVisibleCheck(unittest.TestCase):
    def test_outline_none_with_no_focus_visible_fails(self):
        f = audit('<style>button{outline:none}</style>')
        self.assertTrue(f["outline_no_focus_visible"])
        self.assertTrue(g._has_fail(f))

    def test_outline_0_with_no_focus_visible_fails(self):
        f = audit('<style>button{outline: 0}</style>')
        self.assertTrue(f["outline_no_focus_visible"])

    def test_outline_none_with_focus_visible_passes(self):
        f = audit('<style>button{outline:none}button:focus-visible{outline:2px solid blue}</style>')
        self.assertFalse(f["outline_no_focus_visible"])
        self.assertFalse(g._has_fail(f))

    def test_no_outline_rule_at_all_passes(self):
        f = audit('<style>button{color:red}</style>')
        self.assertFalse(f["outline_no_focus_visible"])

    def test_outline_none_inside_comment_passes(self):
        f = audit('<style>/* button{outline:none} legacy, removed */ button{color:red}</style>')
        self.assertFalse(f["outline_no_focus_visible"])


# ---- heading-level skip -------------------------------------------

class HeadingSkipCheck(unittest.TestCase):
    def test_h2_to_h4_fails(self):
        f = audit('<h2>a</h2><h4>b</h4>')
        self.assertEqual(len(f["heading_skip"]), 1)
        self.assertTrue(g._has_fail(f))

    def test_sequential_levels_pass(self):
        f = audit('<h1>a</h1><h2>b</h2><h3>c</h3>')
        self.assertEqual(f["heading_skip"], [])
        self.assertFalse(g._has_fail(f))

    def test_level_going_up_passes(self):
        f = audit('<h3>a</h3><h1>b</h1>')
        self.assertEqual(f["heading_skip"], [])

    def test_repeated_level_passes(self):
        f = audit('<h2>a</h2><h2>b</h2>')
        self.assertEqual(f["heading_skip"], [])

    def test_single_heading_passes(self):
        f = audit('<h3>only heading</h3>')
        self.assertEqual(f["heading_skip"], [])


# ---- interactive control with no accessible name -----------------

class AccessibleNameCheck(unittest.TestCase):
    def test_empty_button_fails(self):
        f = audit('<button></button>')
        self.assertEqual(len(f["no_name_fail"]), 1)
        self.assertTrue(g._has_fail(f))

    def test_icon_only_background_image_button_fails(self):
        f = audit('<button class="icon-save"></button>')
        self.assertEqual(len(f["no_name_fail"]), 1)

    def test_button_with_text_passes(self):
        f = audit('<button>Save</button>')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])

    def test_button_with_aria_label_passes(self):
        f = audit('<button aria-label="Save"></button>')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])

    def test_link_with_href_and_no_name_fails(self):
        f = audit('<a href="/x"></a>')
        self.assertEqual(len(f["no_name_fail"]), 1)

    def test_bare_anchor_without_href_not_a_target(self):
        # An <a> with no href is not focusable/interactive at all.
        f = audit('<a name="anchor"></a>')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])

    def test_button_wrapping_img_with_alt_warns_not_fails(self):
        # The false-positive case the named-controls check is designed to survive: has child
        # content, so it lands in WARN rather than FAIL, per module docstring.
        f = audit('<button><img src="save.svg" alt="Save"></button>')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])  # non-empty alt clears it entirely

    def test_button_wrapping_img_with_empty_alt_warns(self):
        f = audit('<button><img src="icon.svg" alt=""></button>')
        self.assertEqual(len(f["no_name_warn"]), 1)
        self.assertEqual(f["no_name_fail"], [])
        self.assertFalse(g._has_fail(f))  # WARN only, not blocking

    def test_button_wrapping_svg_title_clears(self):
        f = audit('<button><svg><title>Save</title></svg></button>')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])

    def test_button_wrapping_bare_svg_warns(self):
        f = audit('<button><svg><path d="M0 0"/></svg></button>')
        self.assertEqual(len(f["no_name_warn"]), 1)

    def test_input_not_flagged(self):
        # Deliberately out of scope: see point 8 in checks/web.py.
        f = audit('<input type="text">')
        self.assertEqual(f["no_name_fail"], [])
        self.assertEqual(f["no_name_warn"], [])


# ---- page title ------------------------------------------

class TitleCheck(unittest.TestCase):
    def test_no_title_element_fails(self):
        html = '<!DOCTYPE html><html lang="en"><head></head><body>x</body></html>'
        f = audit(html)
        self.assertTrue(f["missing_title"])
        self.assertIn("no <title>", f["missing_title_detail"])
        self.assertTrue(g._has_fail(f))

    def test_empty_title_fails(self):
        html = '<!DOCTYPE html><html lang="en"><head><title>  </title></head><body>x</body></html>'
        f = audit(html)
        self.assertTrue(f["missing_title"])
        self.assertIn("empty", f["missing_title_detail"])

    def test_populated_title_passes(self):
        f = audit('<p>wrapped by DOC_OPEN, which carries a title</p>')
        self.assertFalse(f["missing_title"])
        self.assertFalse(g._has_fail(f))

    def test_fragment_without_html_not_flagged(self):
        # A fragment has no <head>; whatever wraps it owns the title.
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write('<section>a</section><section>b</section>')
            path = fh.name
        try:
            f = g.audit_html(path)
        finally:
            Path(path).unlink()
        self.assertFalse(f["missing_title"])
        self.assertFalse(f["missing_landmark"])


# ---- bypass block ----------------------------------------

class LandmarkCheck(unittest.TestCase):
    TWO = '<section><h2>a</h2></section><section><h2>b</h2></section>'

    def test_two_sections_no_main_no_skip_fails(self):
        f = audit(self.TWO)
        self.assertTrue(f["missing_landmark"])
        self.assertEqual(f["section_count"], 2)
        self.assertTrue(g._has_fail(f))

    def test_one_section_passes(self):
        f = audit('<section><h2>only</h2></section>')
        self.assertFalse(f["missing_landmark"])

    def test_main_landmark_passes(self):
        f = audit('<main>' + self.TWO + '</main>')
        self.assertFalse(f["missing_landmark"])
        self.assertFalse(g._has_fail(f))

    def test_role_main_passes(self):
        f = audit('<div role="main">' + self.TWO + '</div>')
        self.assertFalse(f["missing_landmark"])

    def test_skip_link_text_passes(self):
        f = audit('<a href="#content">Skip to content</a>' + self.TWO)
        self.assertFalse(f["missing_landmark"])

    def test_skip_link_aria_label_passes(self):
        f = audit('<a href="#content" aria-label="Skip navigation"><span></span></a>' + self.TWO)
        self.assertFalse(f["missing_landmark"])

    def test_anchor_without_hash_is_not_a_skip_link(self):
        f = audit('<a href="/skip">skip</a>' + self.TWO)
        self.assertTrue(f["missing_landmark"])


# ---- table headers ---------------------------------------

class TableHeaderCheck(unittest.TestCase):
    def test_table_with_no_th_no_caption_fails(self):
        f = audit('<table><tr><td>a</td><td>b</td></tr></table>')
        self.assertEqual(f["table_no_headers"], [1])
        self.assertTrue(g._has_fail(f))

    def test_th_without_scope_still_fails(self):
        f = audit('<table><tr><th>a</th><td>b</td></tr></table>')
        self.assertEqual(len(f["table_no_headers"]), 1)

    def test_th_with_scope_passes(self):
        f = audit('<table><tr><th scope="col">a</th><td>b</td></tr></table>')
        self.assertEqual(f["table_no_headers"], [])
        self.assertFalse(g._has_fail(f))

    def test_caption_passes(self):
        f = audit('<table><caption>Sizes</caption><tr><td>a</td></tr></table>')
        self.assertEqual(f["table_no_headers"], [])

    def test_role_presentation_exempt(self):
        f = audit('<table role="presentation"><tr><td>a</td></tr></table>')
        self.assertEqual(f["table_no_headers"], [])
        self.assertFalse(g._has_fail(f))

    def test_role_none_exempt(self):
        f = audit('<table role="none"><tr><td>a</td></tr></table>')
        self.assertEqual(f["table_no_headers"], [])

    def test_nested_tables_judged_independently(self):
        html = ('<table><caption>outer</caption><tr><td>'
                '<table><tr><td>inner</td></tr></table>'
                '</td></tr></table>')
        f = audit(html)
        self.assertEqual(len(f["table_no_headers"]), 1)

    def test_no_table_passes(self):
        f = audit('<p>no tables</p>')
        self.assertEqual(f["table_no_headers"], [])


# ---- non-text contrast -----------------------------------

class UiContrastCheck(unittest.TestCase):
    def _manifest(self, ui):
        return ('<script type="application/json" id="wcag-contrast-manifest">'
                '{"text_pairings":[{"label":"body","fg":"#1A1A1A","bg":"#FFFFFF"}],'
                f'"ui_pairings":{ui}}}</script>')

    def test_ui_pairing_under_3_to_1_fails(self):
        # 2.5:1 text-floor-irrelevant gray, clearly under 3:1
        f = audit(self._manifest('[{"label":"rule","fg":"#A8A8A8","bg":"#FFFFFF"}]'))
        self.assertTrue(f["ui_pairings_declared"])
        self.assertEqual(len(f["ui_contrast_failures"]), 1)
        self.assertTrue(g._has_fail(f))

    def test_ui_pairing_between_3_and_4_5_passes(self):
        # 3.03:1: fails the 4.5 text floor, clears the 3.0 UI floor: the
        # check must use UI_FLOOR, not the text floor.
        f = audit(self._manifest('[{"label":"icon","fg":"#949494","bg":"#FFFFFF"}]'))
        self.assertEqual(f["ui_contrast_failures"], [])
        self.assertFalse(g._has_fail(f))

    def test_ui_skipped_pairing_fails(self):
        f = audit(self._manifest('[{"label":"axis","fg":"#333333","bg":"linear-gradient(x)"}]'))
        self.assertEqual(len(f["ui_contrast_skipped"]), 1)
        self.assertTrue(g._has_fail(f))

    def test_ui_pairings_absent_is_not_declared_not_failed(self):
        f = audit(ContrastCheck.MANIFEST_OK)
        self.assertTrue(f["manifest_present"])
        self.assertFalse(f["ui_pairings_declared"])
        self.assertFalse(g._has_fail(f))

    def test_ui_floor_is_3(self):
        self.assertEqual(g.UI_FLOOR, 3.0)


# ---- language of parts (WARN) ------------------------------

class LangPartsCheck(unittest.TestCase):
    META = '<meta name="content-languages" content="en, es">'

    def test_declared_language_with_no_part_warns(self):
        f = audit(self.META + '<p>English only</p>')
        self.assertEqual(len(f["lang_parts_warn"]), 1)
        self.assertIn('"es"', f["lang_parts_warn"][0])
        self.assertTrue(g._has_warn(f))
        self.assertFalse(g._has_fail(f))

    def test_declared_language_with_part_passes(self):
        f = audit(self.META + '<p>English</p><p lang="es">Español</p>')
        self.assertEqual(f["lang_parts_warn"], [])
        self.assertFalse(g._has_warn(f))

    def test_regional_subtag_satisfies_primary(self):
        f = audit(self.META + '<p lang="es-MX">Español</p>')
        self.assertEqual(f["lang_parts_warn"], [])

    def test_undeclared_part_language_warns(self):
        f = audit(self.META + '<p lang="es">a</p><p lang="fr">b</p>')
        self.assertEqual(len(f["lang_parts_warn"]), 1)
        self.assertIn('lang="fr"', f["lang_parts_warn"][0])

    def test_no_meta_is_not_declared_and_silent(self):
        f = audit('<p lang="es">Español without a declaration</p>')
        self.assertFalse(f["content_languages_declared"])
        self.assertEqual(f["lang_parts_warn"], [])
        self.assertFalse(g._has_warn(f))

    def test_page_language_itself_needs_no_part(self):
        f = audit('<meta name="content-languages" content="en">')
        self.assertEqual(f["lang_parts_warn"], [])

    def test_fragment_uses_first_declared_as_page_language(self):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(self.META + '<p>fragment</p>')
            path = fh.name
        try:
            f = g.audit_html(path)
        finally:
            Path(path).unlink()
        self.assertEqual(len(f["lang_parts_warn"]), 1)
        self.assertIn('"es"', f["lang_parts_warn"][0])


class ExitCodes(unittest.TestCase):
    def _run(self, body):
        html = body if "<html" in body else DOC_OPEN + body + DOC_CLOSE
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
            fh.write(html)
            path = fh.name
        try:
            return g.main(["prog", path])
        finally:
            Path(path).unlink()

    def test_clean_exits_0(self):
        self.assertEqual(self._run('<p>clean</p>'), 0)

    def test_fail_exits_1(self):
        self.assertEqual(self._run('<img src="x.png">'), 1)

    def test_warn_only_exits_3(self):
        self.assertEqual(self._run('<button><img src="icon.svg" alt=""></button>'), 3)

    def test_lang_parts_warn_exits_3(self):
        self.assertEqual(self._run(LangPartsCheck.META + '<p>en only</p>'), 3)

    def test_table_fail_exits_1(self):
        self.assertEqual(self._run('<table><tr><td>a</td></tr></table>'), 1)

    def test_bad_invocation_exits_2(self):
        self.assertEqual(g.main(["prog"]), 2)

