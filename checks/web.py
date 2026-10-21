"""web.py: thirteen accessibility checks on a single HTML page.

Every check sits on the scriptable side of one line: whether something is present or absent
can be checked by a script; whether it is accurate needs a person. Where a check straddles
that line it warns rather than fails.

  1. Image alt text (WCAG 1.1.1), FAIL. Every <img> carries an alt attribute. alt="" is the
     correct signal for a decorative image and passes; no alt attribute at all fails, because
     a screen reader then reads out the file name.
  2. Text contrast (WCAG 1.4.3), FAIL. The page declares its color pairings as data, in a
     block the check reads:

       <script type="application/json" id="wcag-contrast-manifest">
       { "floor": 4.5,
         "text_pairings": [ { "label": "body", "fg": "#1A202C", "bg": "#FFFFFF" } ] }
       </script>

     Each pairing must clear the floor (4.5:1 by default). A pairing over a photo or a
     gradient cannot be resolved from the source and fails rather than passing silently.
     A page with no declared pairings is not failed, but is reported as "not declared".
  3. Tab order (WCAG 2.4.3), FAIL. No positive tabindex, which pulls an element out of
     reading order.
  4. Hidden focusable elements (WCAG 4.1.2), FAIL. Nothing a keyboard can reach carries
     aria-hidden="true".
  5. Page language (WCAG 3.1.1), FAIL. <html> carries a lang attribute. A fragment with no
     <html> tag is not checked: whatever wraps it owns the language.
  6. Visible keyboard focus (WCAG 2.4.7), FAIL. If the stylesheet removes the focus outline,
     a :focus-visible rule exists somewhere in the file.
  7. Heading structure (WCAG 1.3.1), FAIL. Headings never skip a level going down (h2
     straight to h4).
  8. Named controls (WCAG 4.1.2). A <button> or <a href> with no text, no child elements and
     no aria-label, aria-labelledby or title FAILS. One with child content but no name the
     check can find WARNS, because an icon's name may come from somewhere the check does not
     resolve.
  9. Page title (WCAG 2.4.2), FAIL. A full page has a non-empty <title>.
 10. Bypass blocks (WCAG 2.4.1), FAIL. A page with more than one <section> has a <main>
     landmark or a skip link.
 11. Table headers (WCAG 1.3.1), FAIL. Every data table has a <caption> or a <th scope>.
     A table marked role="presentation" is layout by the author's declaration and exempt.
 12. Non-text contrast (WCAG 1.4.11), FAIL. When the manifest declares "ui_pairings"
     (icons, rules, focus rings, control borders), each clears 3:1.
 13. Language of parts (WCAG 3.1.2), WARN. A page that declares more than one content
     language (<meta name="content-languages" content="en, es">) marks each passage in the
     second language with its own lang attribute.

The contrast arithmetic lives in checks/contrast.py. Everything else is standard library.

    python3 -m checks.web FILE.html [FILE2.html ...]

Exit 0 = clean. Exit 1 = at least one FAIL. Exit 2 = bad invocation. Exit 3 = warnings only.
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from checks import contrast

MANIFEST_ID = "wcag-contrast-manifest"

# Void elements never carry an end tag and can never have children. Pushing
# them onto the element stack would never be popped, corrupting parent
# linkage for later elements.
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# Focusable, for A11Y-03/A11Y-04: native interactive tags, or `a` with href,
# or any tag carrying tabindex >= 0.
_FOCUSABLE_NATIVE = {"button", "input", "select", "textarea", "summary"}

# A11Y-01 target scope: button and a[href] only. See module docstring point 8
# for why input/select/textarea are deliberately excluded.
_NAME_TARGET_TAGS = {"button"}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# A11Y-12: WCAG 1.4.11 non-text contrast floor. Fixed, not manifest-overridable:
# this one is the WCAG AA value itself.
UI_FLOOR = 3.0

# A11Y-13: the <meta name> that declares an artifact's content languages.
CONTENT_LANGUAGES_META = "content-languages"

# outline:none / outline:0 / outline-style:none, case-insensitive.
_OUTLINE_NONE_RE = re.compile(r"outline(?:-style)?\s*:\s*(?:none|0)\b", re.I)


def _is_focusable(tag, d):
    if tag in _FOCUSABLE_NATIVE:
        return True
    if tag == "a" and "href" in d:
        return True
    tabindex = d.get("tabindex")
    if tabindex is None:
        return False
    try:
        return int(tabindex.strip()) >= 0
    except ValueError:
        return False


def _lang_primary(tag):
    """BCP 47 primary subtag, lowercased: 'es-MX' -> 'es'. Empty -> ''."""
    return tag.strip().lower().split("-")[0]


def _strip_css_comments(text):
    """Remove /* ... */ blocks from CSS text so a comment mentioning
    outline:none does not false-fire the A11Y-02 check."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


class _A11yParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        # existing (alt + contrast manifest)
        self.imgs_missing_alt = []
        self.manifest_text = None
        self._in_manifest = False
        # A11Y-02: stylesheet text
        self.style_text = ""
        self._in_style = False
        # A11Y-05
        self.html_seen = False
        self.missing_lang_line = None
        # A11Y-04
        self.positive_tabindex = []
        # A11Y-03
        self.aria_hidden_focusable = []
        # A11Y-06
        self._last_heading_level = None
        self.heading_skips = []
        # A11Y-01
        self.no_name_fail = []
        self.no_name_warn = []
        self.stack = []  # open-element frames for A11Y-01 + svg>title detection
        # A11Y-09
        self.title_seen = False
        self.title_text = ""
        self._in_title = False
        # A11Y-10
        self.main_seen = False
        self.section_count = 0
        self.skip_link_seen = False
        # A11Y-11
        self._table_stack = []
        self.tables_no_headers = []
        self.table_count = 0
        # A11Y-13
        self.html_lang = ""
        self.content_languages = None   # None = meta absent; list once declared
        self.part_langs = []            # (line, tag, lang) for every non-<html> lang

    # ---- per-attribute checks, run on every start tag regardless of void ----
    def _check_html_lang(self, tag, d, line):
        if tag != "html":
            return
        self.html_seen = True
        self.html_lang = d.get("lang", "").strip()
        if not self.html_lang:
            self.missing_lang_line = line

    def _check_landmarks(self, tag, d):
        if tag == "main" or d.get("role", "").strip().lower() == "main":
            self.main_seen = True
        if tag == "section":
            self.section_count += 1
        if tag == "a" and d.get("href", "").startswith("#") and "skip" in d.get("aria-label", "").lower():
            self.skip_link_seen = True

    def _check_table_start(self, tag, d, line):
        if tag == "table":
            self.table_count += 1
            role = d.get("role", "").strip().lower()
            self._table_stack.append({
                "line": line, "has_caption": False, "has_th_scope": False,
                "presentational": role in ("presentation", "none"),
            })
            return
        if not self._table_stack:
            return
        if tag == "caption":
            self._table_stack[-1]["has_caption"] = True
        elif tag == "th" and d.get("scope", "").strip():
            self._table_stack[-1]["has_th_scope"] = True

    def _check_table_end(self, tag):
        if tag != "table" or not self._table_stack:
            return
        frame = self._table_stack.pop()
        if frame["presentational"]:
            return
        if not (frame["has_caption"] or frame["has_th_scope"]):
            self.tables_no_headers.append(frame["line"])

    def _check_lang_parts(self, tag, d, line):
        if tag == "meta" and d.get("name", "").strip().lower() == CONTENT_LANGUAGES_META:
            self.content_languages = [
                t.strip() for t in d.get("content", "").split(",") if t.strip()]
            return
        if tag != "html" and d.get("lang", "").strip():
            self.part_langs.append((line, tag, d["lang"].strip()))

    def _check_positive_tabindex(self, tag, d, line):
        if "tabindex" not in d:
            return
        try:
            tv = int(d["tabindex"].strip())
        except ValueError:
            return
        if tv > 0:
            self.positive_tabindex.append((line, tag, tv))

    def _check_aria_hidden(self, tag, d, line):
        if d.get("aria-hidden", "").strip().lower() != "true":
            return
        if _is_focusable(tag, d):
            self.aria_hidden_focusable.append((line, tag))

    def _check_heading_skip(self, tag, line):
        if tag not in HEADING_TAGS:
            return
        level = int(tag[1])
        if self._last_heading_level is not None and level > self._last_heading_level + 1:
            self.heading_skips.append((line, self._last_heading_level, level))
        self._last_heading_level = level

    # ---- A11Y-01 accessible-name bookkeeping -------------------------------
    def _mark_name_contribution(self):
        """A descendant name contributor (non-empty <img alt>, or svg>title
        text) clears every currently open ancestor frame."""
        for frame in self.stack:
            frame["has_name"] = True

    def _finalize_target(self, frame):
        if frame["has_name"]:
            return
        if not frame["has_child_elem"] and not frame["has_text"]:
            self.no_name_fail.append((frame["line"], frame["tag"]))
        else:
            self.no_name_warn.append((frame["line"], frame["tag"]))

    # ---- HTMLParser hooks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        d = {k: (v or "") for k, v in attrs}
        line, _col = self.getpos()

        self._check_html_lang(tag, d, line)
        self._check_positive_tabindex(tag, d, line)
        self._check_aria_hidden(tag, d, line)
        self._check_heading_skip(tag, line)
        self._check_landmarks(tag, d)
        self._check_table_start(tag, d, line)
        self._check_lang_parts(tag, d, line)
        if tag == "title":
            self.title_seen = True
            self._in_title = True

        if tag == "img":
            if "alt" not in d:
                self.imgs_missing_alt.append(line)
            elif d.get("alt", "").strip():
                self._mark_name_contribution()
            if self.stack:
                self.stack[-1]["has_child_elem"] = True
            return

        if tag == "script" and d.get("type") == "application/json" and d.get("id") == MANIFEST_ID:
            self._in_manifest = True
        if tag == "style":
            self._in_style = True

        if tag in VOID_ELEMENTS:
            if self.stack:
                self.stack[-1]["has_child_elem"] = True
            return

        is_target = tag in _NAME_TARGET_TAGS or (tag == "a" and "href" in d)
        has_own_name = bool(
            d.get("aria-label", "").strip()
            or d.get("aria-labelledby", "").strip()
            or d.get("title", "").strip()
        )
        if self.stack:
            self.stack[-1]["has_child_elem"] = True
        self.stack.append({
            "tag": tag, "line": line, "is_target": is_target,
            "has_child_elem": False, "has_text": False, "has_name": has_own_name,
            "hash_anchor": tag == "a" and d.get("href", "").startswith("#"),
        })

    def handle_startendtag(self, tag, attrs):
        # Self-closed form (<button/>, <br/>): run the same start-tag logic,
        # then immediately close anything it pushed. No separate end tag
        # will ever arrive for it. Net effect matches an open+close pair.
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1]["tag"] == tag:
            frame = self.stack.pop()
            if frame["is_target"]:
                self._finalize_target(frame)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_manifest = False
        if tag == "style":
            self._in_style = False
        if tag == "title":
            self._in_title = False
        self._check_table_end(tag)
        if self.stack and self.stack[-1]["tag"] == tag:
            frame = self.stack.pop()
            if frame["is_target"]:
                self._finalize_target(frame)

    def handle_data(self, data):
        if self._in_manifest:
            self.manifest_text = (self.manifest_text or "") + data
        if self._in_style:
            self.style_text += data
        if self._in_title:
            self.title_text += data
        if not data.strip():
            return
        if "skip" in data.lower() and any(fr["hash_anchor"] for fr in self.stack):
            self.skip_link_seen = True
        # Any visible text within an interactive control's subtree is itself
        # a legitimate accessible-name source (the accessible-name algorithm
        # reads content text), so it clears the check outright rather than
        # merely avoiding the FAIL branch. This also covers svg > title text
        # (module docstring point 8) without a special case.
        for frame in self.stack:
            frame["has_text"] = True
        self._mark_name_contribution()


def audit_html(path, floor=None):
    """Return a findings dict. `floor` is the text floor for a manifest that declares none
    (default contrast.FLOOR).

    FAIL keys: alt_missing, contrast_failures, contrast_skipped,
    manifest_error, positive_tabindex, aria_hidden_focusable, missing_lang,
    missing_lang_detail, heading_skip, no_name_fail, outline_no_focus_visible,
    missing_title, missing_title_detail, missing_landmark,
    missing_landmark_detail, table_no_headers, ui_contrast_failures,
    ui_contrast_skipped.
    WARN keys: no_name_warn, lang_parts_warn.
    Informational: manifest_present, ui_pairings_declared, section_count,
    content_languages_declared, content_languages.
    """
    text = Path(path).read_text(encoding="utf-8")
    parser = _A11yParser()
    parser.feed(text)
    parser.close()

    findings = {
        "alt_missing": parser.imgs_missing_alt,
        "contrast_failures": [], "contrast_skipped": [], "manifest_error": None,
        "manifest_present": False,
        "positive_tabindex": parser.positive_tabindex,
        "aria_hidden_focusable": parser.aria_hidden_focusable,
        "missing_lang": False, "missing_lang_detail": None,
        "heading_skip": parser.heading_skips,
        "no_name_fail": parser.no_name_fail,
        "outline_no_focus_visible": False,
        "no_name_warn": parser.no_name_warn,
        "missing_title": False, "missing_title_detail": None,
        "missing_landmark": False, "missing_landmark_detail": None,
        "section_count": parser.section_count,
        "table_no_headers": parser.tables_no_headers,
        "ui_pairings_declared": False,
        "ui_contrast_failures": [], "ui_contrast_skipped": [],
        "content_languages_declared": parser.content_languages is not None,
        "content_languages": parser.content_languages or [],
        "lang_parts_warn": [],
    }

    if parser.html_seen:
        if not parser.title_seen:
            findings["missing_title"] = True
            findings["missing_title_detail"] = "no <title> element"
        elif not parser.title_text.strip():
            findings["missing_title"] = True
            findings["missing_title_detail"] = "<title> is empty"
        if parser.section_count > 1 and not (parser.main_seen or parser.skip_link_seen):
            findings["missing_landmark"] = True
            findings["missing_landmark_detail"] = (
                f"{parser.section_count} <section> elements but no <main> landmark and no "
                f"skip link")

    if parser.content_languages is not None:
        declared = [_lang_primary(t) for t in parser.content_languages]
        page = _lang_primary(parser.html_lang) if parser.html_lang else (declared[0] if declared else "")
        present = {_lang_primary(lang) for _line, _tag, lang in parser.part_langs}
        for tag_, prim in zip(parser.content_languages, declared):
            if prim and prim != page and prim not in present:
                findings["lang_parts_warn"].append(
                    f'content-languages declares "{tag_}" but no element carries lang="{prim}"')
        for line, tag, lang in parser.part_langs:
            prim = _lang_primary(lang)
            if prim and prim != page and prim not in declared:
                findings["lang_parts_warn"].append(
                    f'<{tag}> (line {line}) carries lang="{lang}" not listed in content-languages')

    if parser.html_seen and parser.missing_lang_line is not None:
        findings["missing_lang"] = True
        findings["missing_lang_detail"] = f"<html> missing lang attribute (line {parser.missing_lang_line})"

    if parser.manifest_text and parser.manifest_text.strip():
        findings["manifest_present"] = True
        try:
            manifest = json.loads(parser.manifest_text)
        except json.JSONDecodeError as exc:
            findings["manifest_error"] = f"manifest is not valid JSON: {exc}"
        else:
            floor = manifest.get("floor", contrast.FLOOR if floor is None else floor)
            pairings = [(p["label"], p["fg"], p["bg"]) for p in manifest.get("text_pairings", [])]
            findings["contrast_failures"], findings["contrast_skipped"] = contrast.check_contrast(
                pairings, floor=floor)
            if "ui_pairings" in manifest:
                findings["ui_pairings_declared"] = True
                ui = [(p["label"], p["fg"], p["bg"]) for p in manifest["ui_pairings"]]
                findings["ui_contrast_failures"], findings["ui_contrast_skipped"] = (
                    contrast.check_contrast(ui, floor=UI_FLOOR))

    css_clean = _strip_css_comments(parser.style_text)
    has_outline_none = bool(_OUTLINE_NONE_RE.search(css_clean))
    has_focus_visible = ":focus-visible" in css_clean
    findings["outline_no_focus_visible"] = has_outline_none and not has_focus_visible

    return findings


def _has_fail(f):
    return bool(
        f["alt_missing"] or f["contrast_failures"] or f["contrast_skipped"]
        or f["manifest_error"] or f["positive_tabindex"] or f["aria_hidden_focusable"]
        or f["missing_lang"] or f["heading_skip"] or f["no_name_fail"]
        or f["outline_no_focus_visible"]
        or f["missing_title"] or f["missing_landmark"] or f["table_no_headers"]
        or f["ui_contrast_failures"] or f["ui_contrast_skipped"]
    )


def _has_warn(f):
    return bool(f["no_name_warn"] or f["lang_parts_warn"])


def _undeclared_note(f):
    """The things this file never declared, so 'not declared' is printed
    rather than inferred: the contrast manifest, ui_pairings, content-languages."""
    missing = []
    if not f["manifest_present"]:
        missing.append("no contrast manifest embedded")
    elif not f["ui_pairings_declared"]:
        missing.append("ui_pairings not declared")
    if not f["content_languages_declared"]:
        missing.append("content-languages not declared")
    return f"  (NOTE: {'; '.join(missing)})" if missing else ""


def main(argv):
    files = argv[1:]
    if not files:
        print("usage: python3 -m checks.web FILE.html [FILE2.html ...]", file=sys.stderr)
        return 2
    any_fail = any_warn = False
    for path in files:
        try:
            f = audit_html(path)
        except OSError as exc:
            print(f"FAIL  {path}: {exc}")
            any_fail = True
            continue

        fail, warn = _has_fail(f), _has_warn(f)
        any_fail = any_fail or fail
        any_warn = any_warn or warn

        if not fail and not warn:
            print(f"OK    {path}: all checks clear{_undeclared_note(f)}")
            continue

        label = "FAIL" if fail else "WARN"
        print(f"{label}  {path}")
        for line in f["alt_missing"]:
            print(f"        FAIL <img> missing alt attribute (line {line})")
        if f["manifest_error"]:
            print(f"        FAIL {f['manifest_error']}")
        for label_, fg, bg, r in f["contrast_failures"]:
            print(f"        FAIL contrast: {label_}: {fg} on {bg} = {r}:1")
        for label_, fg, bg in f["contrast_skipped"]:
            print(f"        FAIL contrast: {label_}: skipped, non-solid pairing {fg} on {bg}")
        for line, tag, tv in f["positive_tabindex"]:
            print(f"        FAIL positive tabindex={tv} on <{tag}> (line {line}): reorders tab sequence")
        for line, tag in f["aria_hidden_focusable"]:
            print(f"        FAIL aria-hidden=\"true\" on focusable <{tag}> (line {line})")
        if f["missing_lang"]:
            print(f"        FAIL {f['missing_lang_detail']}")
        for line, from_level, to_level in f["heading_skip"]:
            print(f"        FAIL heading level skip: h{from_level} to h{to_level} (line {line}); "
                  f"style a lower level instead of skipping")
        for line, tag in f["no_name_fail"]:
            print(f"        FAIL <{tag}> (line {line}) has no accessible name: no text, no child "
                  f"elements, no aria-label/aria-labelledby/title")
        if f["outline_no_focus_visible"]:
            print("        FAIL outline:none/0 present with no :focus-visible selector anywhere in "
                  "the file: keyboard focus is invisible")
        if f["missing_title"]:
            print(f"        FAIL {f['missing_title_detail']}: a page needs a non-empty <title>")
        if f["missing_landmark"]:
            print(f"        FAIL {f['missing_landmark_detail']}: wrap the content in <main> or "
                  f"add a skip link")
        for line in f["table_no_headers"]:
            print(f"        FAIL <table> (line {line}) has no <caption> and no <th scope>: mark "
                  f"headers, or role=\"presentation\" for a layout table")
        for label_, fg, bg, r in f["ui_contrast_failures"]:
            print(f"        FAIL non-text contrast: {label_}: {fg} on {bg} = {r}:1 (floor {UI_FLOOR}:1)")
        for label_, fg, bg in f["ui_contrast_skipped"]:
            print(f"        FAIL non-text contrast: {label_}: skipped, non-solid pairing {fg} on {bg}")
        for line, tag in f["no_name_warn"]:
            print(f"        WARN <{tag}> (line {line}) has child content but no detected accessible "
                  f"name: verify img alt / svg title / aria-label by hand")
        for msg in f["lang_parts_warn"]:
            print(f"        WARN language of parts: {msg}")
        if not fail:
            print(f"        (all FAIL-tier checks clear{_undeclared_note(f)})")

    if any_fail:
        return 1
    if any_warn:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
