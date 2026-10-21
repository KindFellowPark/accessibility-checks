"""report.py: turns the web checks' findings into a report a non-technical reader can act on.

The checks themselves print terse pass/fail lines. That output proves little to someone who
has to sign off: "all checks clear" reads the same whether a check examined eleven color
pairings or nothing at all. This report imports the same audit_html call the command line
uses, so the report and the check can never disagree, and adds three things:

  1. A count of what each check examined. A check with nothing to examine reads "not
     applicable", never green. Contrast is the exception: every page has text on a
     background, so a page that declares no pairings reads "not declared", unproven rather
     than clean.
  2. Contrast evidence. Each declared pairing is drawn as a swatch (the real text color on
     the real background) beside its measured ratio and its margin over the floor, tightest
     first.
  3. Proof the checks fire. A check that has never failed has not been tested. For each
     check, the report writes a throwaway copy of the page with one planted defect of that
     kind, re-runs the checks on it, and records whether the check caught it. This runs every
     time a report is built; it is never a hardcoded claim.

The report is itself a web page, so it is held to the same checks before it is written out.

    python3 -m checks.report PAGE.html [-o OUT.html]

Exit 0 = report written (whatever the page's own verdict). Exit 1 = could not build it.
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from checks import contrast
from checks import web as gate


# Each entry: an internal id, the WCAG success criterion it covers, a plain-English name and
# rule, the findings keys that make a failure, the count key naming what the check examines,
# and the singular/plural noun for that count.
CHECKS = [
    {
        "id": "alt", "wcag": "WCAG 1.1.1",
        "name": "Image alternative text",
        "rule": "Every image carries an alt attribute. An intentionally decorative image "
                "uses alt=\"\"; an image with no alt at all makes a screen reader read the "
                "file name aloud.",
        "fail_keys": ["alt_missing"],
        "census": "img",
        "noun": ("image", "images"),
    },
    {
        "id": "contrast", "wcag": "WCAG 1.4.3",
        "name": "Text contrast",
        "rule": f"Every text-on-background pairing the page declares clears {contrast.FLOOR}:1. "
                "The floor applies to all text, large text included.",
        "fail_keys": ["contrast_failures", "contrast_skipped", "manifest_error"],
        "census": "pairing",
        "noun": ("color pairing", "color pairings"),
        "undeclared": (
            "This page declares no color pairings, so none could be verified. "
            "Contrast is unproven here, not proven clean."),
    },
    {
        "id": "tabindex", "wcag": "WCAG 2.4.3",
        "name": "Tab order",
        "rule": "No element carries a positive tabindex. A positive value pulls an element "
                "out of reading order in the keyboard tab sequence.",
        "fail_keys": ["positive_tabindex"],
        "census": "tabindex",
        "noun": ("tabindex attribute", "tabindex attributes"),
    },
    {
        "id": "aria_hidden", "wcag": "WCAG 4.1.2",
        "name": "Hidden focusable elements",
        "rule": "Nothing a keyboard can reach is hidden from screen readers. An element that "
                "is focusable but aria-hidden traps a keyboard user on something unannounced.",
        "fail_keys": ["aria_hidden_focusable"],
        "census": "aria_hidden",
        "noun": ("aria-hidden element", "aria-hidden elements"),
    },
    {
        "id": "lang", "wcag": "WCAG 3.1.1",
        "name": "Page language",
        "rule": "The page declares its language, so a screen reader picks the right "
                "pronunciation rules instead of guessing.",
        "fail_keys": ["missing_lang"],
        "census": "html_tag",
        "noun": ("page root", "page roots"),
    },
    {
        "id": "focus", "wcag": "WCAG 2.4.7",
        "name": "Visible keyboard focus",
        "rule": "If the default focus outline is removed, a :focus-visible replacement exists. "
                "Otherwise a keyboard user cannot see where they are on the page.",
        "fail_keys": ["outline_no_focus_visible"],
        "census": "stylesheet",
        "noun": ("stylesheet", "stylesheets"),
    },
    {
        "id": "headings", "wcag": "WCAG 1.3.1",
        "name": "Heading structure",
        "rule": "Heading levels descend one step at a time. A jump from h2 to h4 breaks the "
                "outline screen-reader users navigate by.",
        "fail_keys": ["heading_skip"],
        "census": "heading",
        "noun": ("heading", "headings"),
    },
    {
        "id": "names", "wcag": "WCAG 4.1.2",
        "name": "Named controls",
        "rule": "Every button and link has a name a screen reader can announce: its text, "
                "an aria-label, or the alt text of an icon inside it.",
        "fail_keys": ["no_name_fail"],
        "warn_keys": ["no_name_warn"],
        "census": "control",
        "noun": ("button or link", "buttons and links"),
    },
    {
        "id": "title", "wcag": "WCAG 2.4.2",
        "name": "Page title",
        "rule": "The page has a non-empty <title>. It is the first thing a screen reader "
                "announces, the browser tab's label, and the document title of any PDF "
                "exported from the page.",
        "fail_keys": ["missing_title"],
        "census": "html_tag",
        "noun": ("page root", "page roots"),
    },
    {
        "id": "bypass", "wcag": "WCAG 2.4.1",
        "name": "Bypass blocks",
        "rule": "A page with more than one section has a <main> landmark or a skip link, "
                "so a keyboard or screen-reader user can jump past repeated blocks instead "
                "of tabbing through them.",
        "fail_keys": ["missing_landmark"],
        "census": "section",
        "noun": ("section", "sections"),
    },
    {
        "id": "tables", "wcag": "WCAG 1.3.1",
        "name": "Table headers",
        "rule": "Every data table has header cells marked with a scope, or a caption. A "
                "layout table marked role=\"presentation\" is exempt; whether it really is "
                "layout is a human call.",
        "fail_keys": ["table_no_headers"],
        "census": "table",
        "noun": ("table", "tables"),
    },
    {
        "id": "ui_contrast", "wcag": "WCAG 1.4.11",
        "name": "Non-text contrast",
        "rule": "Every declared icon, rule, focus ring, chart axis or control border clears "
                "3:1 against what it sits on.",
        "fail_keys": ["ui_contrast_failures", "ui_contrast_skipped"],
        "census": "ui_pairing",
        "noun": ("non-text pairing", "non-text pairings"),
        "undeclared": (
            "This page declares no non-text pairings, so no icon, rule or border could be "
            "verified. Non-text contrast is unproven here, not proven clean."),
    },
    {
        "id": "lang_parts", "wcag": "WCAG 3.1.2",
        "name": "Language of parts",
        "rule": "When the page declares that its content is in more than one language, "
                "every passage in a second language carries its own lang attribute so a "
                "screen reader switches pronunciation. Reported as a warning: whether a "
                "passage is in another language is a human read.",
        "fail_keys": [],
        "warn_keys": ["lang_parts_warn"],
        "census": "content_language",
        "noun": ("declared language", "declared languages"),
        "undeclared": (
            "This page declares no content languages, so the check could not run. "
            "If every word on the page is in its page language that is correct; declare "
            "the list once a second language appears."),
    },
]

# What the checks deliberately do not decide. A report that implies total coverage is worse
# than one that names its edges.
OUT_OF_SCOPE = [
    ("Whether alt text is <em>accurate</em>",
     "The check proves an alt attribute exists. Whether it describes the image is a human read."),
    ("Whether alt=\"\" is <em>appropriate</em>",
     "An empty alt correctly hides a decorative image. Deciding an image is decorative is a "
     "human call."),
    ("Text styled to look like a heading",
     "Bold text standing in for a heading is caught in Word files, not on web pages. On a page, "
     "a person checks that every visual heading is a real heading tag."),
    ("Reading order",
     "The checks look at heading levels, not whether the page's sequence makes sense read aloud."),
    ("Text over photos or gradients",
     "Contrast over a non-solid background cannot be resolved from the source. Such a pairing "
     "is reported as unresolved and fails, rather than passing silently."),
    ("Anything only a live page shows",
     "Focus traps in a pop-up, motion, and screen-reader announcement order need a rendered page "
     "and assistive technology, not a source scan."),
]


# ── subject census ───────────────────────────────────────────────────────────
class _Census(gate._A11yParser):
    """Counts what each check had to look at.

    Subclasses the gate's own parser rather than re-deriving the traversal, so
    the census can never count a different set of elements than the gate checked.
    """

    def __init__(self):
        super().__init__()
        self.counts = {
            "img": 0, "tabindex": 0, "aria_hidden": 0, "html_tag": 0,
            "stylesheet": 0, "heading": 0, "control": 0, "pairing": 0,
            "section": 0, "table": 0, "ui_pairing": 0, "content_language": 0,
        }

    def handle_starttag(self, tag, attrs):
        d = {k: (v or "") for k, v in attrs}
        if tag == "img":
            self.counts["img"] += 1
        if "tabindex" in d:
            self.counts["tabindex"] += 1
        if d.get("aria-hidden", "").strip().lower() == "true":
            self.counts["aria_hidden"] += 1
        if tag == "html":
            self.counts["html_tag"] += 1
        if tag == "style":
            self.counts["stylesheet"] += 1
        if tag in gate.HEADING_TAGS:
            self.counts["heading"] += 1
        if tag == "button" or (tag == "a" and "href" in d):
            self.counts["control"] += 1
        if tag == "section":
            self.counts["section"] += 1
        if tag == "table":
            self.counts["table"] += 1
        super().handle_starttag(tag, attrs)


def census(path):
    parser = _Census()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    parser.close()
    counts = dict(parser.counts)
    manifest = read_manifest(path)
    counts["pairing"] = len(manifest.get("text_pairings", []))
    counts["ui_pairing"] = len(manifest.get("ui_pairings", []))
    counts["content_language"] = len(parser.content_languages or [])
    return counts


def read_manifest(path):
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(
        r'<script[^>]*id="wcag-contrast-manifest"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


# ── negative control ─────────────────────────────────────────────────────────
# One injected defect per check. Each is applied to its own scratch copy so the
# checks are exercised in isolation and a fired check cannot be credited to a
# different injection. A mutation that cannot be applied to this particular
# page is reported as "not exercised", never quietly counted as a pass.
def _inject_before_body_end(text, fragment):
    idx = text.lower().rfind("</body>")
    if idx == -1:
        return None
    return text[:idx] + fragment + text[idx:]


def _mut_alt(text):
    return _inject_before_body_end(
        text, '\n<img src="negative-control.png">\n')


def _mut_contrast(text):
    m = re.search(
        r'(<script[^>]*id="wcag-contrast-manifest"[^>]*>)(.*?)(</script>)', text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(2))
    except json.JSONDecodeError:
        return None
    pairings = data.get("text_pairings")
    if not pairings:
        return None
    # A pale gray on white: plainly readable to a designer's eye,
    # and 1.94:1. The exact class of defect only arithmetic catches.
    pairings[0] = dict(pairings[0], fg="#BDB5A8", bg="#FFFFFF")
    return text[:m.start(2)] + json.dumps(data, indent=2) + text[m.end(2):]


def _mut_tabindex(text):
    return _inject_before_body_end(
        text, '\n<div tabindex="3">negative control</div>\n')


def _mut_aria_hidden(text):
    return _inject_before_body_end(
        text, '\n<a href="#negative-control" aria-hidden="true">negative control</a>\n')


def _mut_lang(text):
    new, n = re.subn(r'(<html\b[^>]*?)\s+lang="[^"]*"', r'\1', text, count=1)
    return new if n else None


def _mut_focus(text):
    m = re.search(r"<style[^>]*>", text)
    if not m:
        return None
    # The check fires on outline:none *with no :focus-visible anywhere in the
    # file*, so adding outline:none to a page that already has a
    # :focus-visible rule is not a defect and correctly does not fire. The
    # planted defect therefore removes the existing rules too, otherwise the
    # control silently reports a miss against a gate that behaved correctly.
    stripped = text.replace(":focus-visible", ":focus-negative-control")
    m = re.search(r"<style[^>]*>", stripped)
    return (stripped[:m.end()]
            + "\n.negative-control:focus { outline: none; }\n"
            + stripped[m.end():])


def _mut_heading(text):
    # An h2 immediately followed by an h4 is a skip no matter what heading
    # level the document happened to end on. Deriving the injected level from
    # an existing heading instead looked correct and silently produced a
    # non-defect on the first page tried (the document's last heading was
    # already at the level being injected), so the planted defect is made
    # self-contained rather than relative to the page.
    return _inject_before_body_end(
        text, "\n<h2>negative control anchor</h2>\n<h4>negative control</h4>\n")


def _mut_name(text):
    return _inject_before_body_end(text, "\n<button></button>\n")


def _mut_title(text):
    new, n = re.subn(r"<title>.*?</title>", "<title></title>", text, count=1, flags=re.S)
    return new if n else None


def _mut_landmark(text):
    # Remove every landmark the check accepts, then make sure there is something
    # to skip past. A skip link is neutralised by renaming its text, since the
    # check reads the anchor's text for the word "skip".
    stripped = re.sub(r"<main\b", "<div data-negative-control", text, flags=re.I)
    stripped = re.sub(r"</main>", "</div>", stripped, flags=re.I)
    stripped = re.sub(r'role="main"', 'data-negative-control="main"', stripped, flags=re.I)
    stripped = re.sub(r'(<a\b[^>]*href="#[^"]*"[^>]*>)([^<]*)(</a>)',
                      lambda m: m.group(1) + m.group(2).replace("skip", "negative control")
                      .replace("Skip", "Negative control") + m.group(3), stripped)
    return _inject_before_body_end(
        stripped, "\n<section><h2>negative control</h2></section>"
                  "<section><h2>negative control</h2></section>\n")


def _mut_table(text):
    return _inject_before_body_end(
        text, "\n<table><tr><td>negative control</td><td>no header</td></tr></table>\n")


def _mut_ui_contrast(text):
    m = re.search(
        r'(<script[^>]*id="wcag-contrast-manifest"[^>]*>)(.*?)(</script>)', text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(2))
    except json.JSONDecodeError:
        return None
    # Declared-and-failing is the only state the check fails on, so the planted
    # defect declares one: 1.94:1, under the 3:1 non-text floor.
    data["ui_pairings"] = [{"label": "negative control rule", "fg": "#BDB5A8", "bg": "#FFFFFF"}]
    return text[:m.start(2)] + json.dumps(data, indent=2) + text[m.end(2):]


def _mut_lang_parts(text):
    # Declare a second language (Esperanto, which no sample page carries) with no
    # element marked in it. Replaces an existing declaration or adds one.
    page = re.search(r'<html\b[^>]*\blang="([^"]*)"', text)
    declared = f"{page.group(1) if page else 'en'}, eo"
    meta = f'<meta name="content-languages" content="{declared}">'
    new, n = re.subn(r'<meta\s+name="content-languages"[^>]*>', meta, text, count=1, flags=re.I)
    if n:
        return new
    m = re.search(r"<head[^>]*>", text)
    if not m:
        return None
    return text[:m.end()] + "\n" + meta + text[m.end():]


MUTATIONS = {
    "alt": ("an <img> with no alt attribute", _mut_alt),
    "contrast": ("one color pairing moved to #BDB5A8 on #FFFFFF (1.94:1)", _mut_contrast),
    "tabindex": ('an element carrying tabindex="3"', _mut_tabindex),
    "aria_hidden": ('a link marked aria-hidden="true"', _mut_aria_hidden),
    "lang": ("the lang attribute stripped from <html>", _mut_lang),
    "focus": ("outline:none added with no :focus-visible rule", _mut_focus),
    "headings": ("a heading level skipped", _mut_heading),
    "names": ("an empty <button></button>", _mut_name),
    "title": ("the <title> emptied", _mut_title),
    "bypass": ("<main> and any skip link removed, two extra sections added", _mut_landmark),
    "tables": ("a <table> with no header cells and no caption", _mut_table),
    "ui_contrast": ("a ui_pairing declared at #BDB5A8 on #FFFFFF (1.94:1)", _mut_ui_contrast),
    "lang_parts": ('content-languages declaring "eo" with no element in it', _mut_lang_parts),
}


def run_negative_control(path, workdir, floor=None):
    """Apply each injection to its own copy, re-run the gate, record whether
    that check's own fail keys fired. Returns {check_id: (defect, status)}."""
    source = Path(path).read_text(encoding="utf-8")
    results = {}
    for check in CHECKS:
        cid = check["id"]
        defect, mutate = MUTATIONS[cid]
        mutated = mutate(source)
        if mutated is None:
            results[cid] = (defect, "not exercised")
            continue
        scratch = Path(workdir) / f"negative_control_{cid}.html"
        scratch.write_text(mutated, encoding="utf-8")
        findings = gate.audit_html(scratch, floor=floor)
        fired = any(findings.get(k) for k in check["fail_keys"] + check.get("warn_keys", []))
        results[cid] = (defect, "caught" if fired else "MISSED")
    return results


# ── verdicts ─────────────────────────────────────────────────────────────────
def check_verdict(check, findings, counts):
    """clear | not applicable | warn | fail, with the detail lines."""
    detail = []
    for key in check["fail_keys"]:
        value = findings.get(key)
        if not value:
            continue
        if key == "alt_missing":
            detail += [f"&lt;img&gt; with no alt attribute, line {ln}" for ln in value]
        elif key == "contrast_failures":
            detail += [f"{esc(lbl)}: {esc(fg)} on {esc(bg)} = {r}:1" for lbl, fg, bg, r in value]
        elif key == "contrast_skipped":
            detail += [f"{esc(lbl)}: unresolved pairing {esc(fg)} on {esc(bg)}" for lbl, fg, bg in value]
        elif key == "manifest_error":
            detail.append(esc(value))
        elif key == "positive_tabindex":
            detail += [f"tabindex={tv} on &lt;{tag}&gt;, line {ln}" for ln, tag, tv in value]
        elif key == "aria_hidden_focusable":
            detail += [f'aria-hidden="true" on focusable &lt;{tag}&gt;, line {ln}'
                       for ln, tag in value]
        elif key == "missing_lang":
            detail.append(esc(findings.get("missing_lang_detail") or "<html> missing lang"))
        elif key == "heading_skip":
            detail += [f"h{a} straight to h{b}, line {ln}" for ln, a, b in value]
        elif key == "no_name_fail":
            detail += [f"&lt;{tag}&gt; with no name, line {ln}" for ln, tag in value]
        elif key == "outline_no_focus_visible":
            detail.append("outline removed with no :focus-visible replacement")
        elif key == "missing_title":
            detail.append(esc(findings.get("missing_title_detail") or "no usable <title>"))
        elif key == "missing_landmark":
            detail.append(esc(findings.get("missing_landmark_detail") or "no <main> or skip link"))
        elif key == "table_no_headers":
            detail += [f"&lt;table&gt; with no header cells and no caption, line {ln}" for ln in value]
        elif key == "ui_contrast_failures":
            detail += [f"{esc(lbl)}: {esc(fg)} on {esc(bg)} = {r}:1 (floor 3:1)" for lbl, fg, bg, r in value]
        elif key == "ui_contrast_skipped":
            detail += [f"{esc(lbl)}: unresolved pairing {esc(fg)} on {esc(bg)}" for lbl, fg, bg in value]
    if detail:
        return "fail", detail
    for key in check.get("warn_keys", []):
        for item in findings.get(key, []):
            if isinstance(item, str):
                detail.append(esc(item))
            else:
                ln, tag = item
                detail.append(f"&lt;{tag}&gt;, line {ln}: verify the name by hand")
    if detail:
        return "warn", detail
    if counts.get(check["census"], 0) == 0:
        if check.get("undeclared"):
            return "not declared", [check["undeclared"]]
        return "not applicable", []
    return "clear", []


def subject_line(check, counts):
    n = counts.get(check["census"], 0)
    singular, plural = check["noun"]
    return f"{n} {singular if n == 1 else plural} examined"


# ── rendering ────────────────────────────────────────────────────────────────
def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def esc(s):
    return html.escape(str(s), quote=True)


def artifact_title(path):
    m = re.search(r"<title>(.*?)</title>",
                  Path(path).read_text(encoding="utf-8"), re.S)
    title = m.group(1).strip() if m else ""
    return title or Path(path).name


CSS = """
:root{
  --navy:#1F3A5F; --slate:#4A5568; --gold:#B7791F; --ink:#1A202C;
  --page:#F7F8FA; --panel:#FFFFFF; --line:#D5DAE1; --muted:#EDF0F4;
  --pass-tint:#E8EEF6; --fail:#8B1E1E; --fail-tint:#FBECEC; --warn-tint:#F6E3B4;
  --font-stack:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
  --font-mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:var(--font-stack);font-size:16px;line-height:1.6;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:48px 28px 96px}
a{color:var(--navy)}
:focus-visible{outline:2px solid var(--navy);outline-offset:2px}

.masthead{border-top:6px solid var(--gold);padding-top:22px;margin-bottom:34px}
.eyebrow{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--slate);font-weight:700;margin:0 0 8px}
h1{font-size:clamp(1.9rem,3.4vw + .8rem,2.6rem);line-height:1.15;margin:0 0 10px;
  letter-spacing:-.015em;color:var(--navy)}
.subject{font-family:var(--font-mono);font-size:.9rem;color:var(--slate);margin:0}

.verdict{display:flex;flex-wrap:wrap;align-items:center;gap:18px;
  border-radius:14px;padding:22px 26px;margin:30px 0 14px}
.verdict.pass{background:var(--pass-tint);border:1px solid var(--navy)}
.verdict.fail{background:var(--fail-tint);border:1px solid var(--fail)}
.verdict .stamp{font-size:1.5rem;font-weight:800;letter-spacing:-.01em;white-space:nowrap}
.verdict.pass .stamp{color:var(--navy)}
.verdict.fail .stamp{color:var(--fail)}
.verdict p{margin:0;flex:1 1 340px;font-size:.98rem}

.scoreboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px;margin:0 0 44px}
.tile{background:var(--panel);border:1px solid var(--line);
  border-radius:12px;padding:16px 18px}
.tile .n{font-size:1.9rem;font-weight:800;line-height:1;letter-spacing:-.02em}
.tile .k{font-size:.74rem;letter-spacing:.09em;text-transform:uppercase;
  color:var(--slate);font-weight:700;margin-top:7px}
.tile.good .n{color:var(--navy)}
.tile.bad .n{color:var(--fail)}
.tile.mute .n{color:var(--slate)}

h2{font-size:1.32rem;letter-spacing:-.01em;margin:0 0 6px;padding-top:10px;color:var(--navy)}
.lede{color:var(--slate);margin:0 0 20px;font-size:.94rem;max-width:70ch}
section{margin-bottom:52px}

.ledger{display:flex;flex-direction:column;gap:10px}
.row{background:var(--panel);border:1px solid var(--line);
  border-radius:12px;padding:16px 18px;display:grid;
  grid-template-columns:1fr auto;gap:6px 18px;align-items:start}
.row.is-fail{border-color:var(--fail);background:var(--fail-tint)}
.row.is-fail .rule,.row.is-fail .subjects{color:var(--ink)}
.row.is-warn{border-color:var(--gold)}
.row .name{font-weight:700;font-size:1rem}
.row .cid{font-family:var(--font-mono);font-size:.74rem;color:var(--slate);
  margin-left:8px;font-weight:400}
.row .rule{grid-column:1;color:var(--slate);font-size:.88rem;max-width:66ch;margin:2px 0 0}
.row .subjects{grid-column:1;font-family:var(--font-mono);font-size:.78rem;
  color:var(--slate);margin-top:6px}
.row .findings{grid-column:1/-1;margin:10px 0 0;padding:12px 14px;
  background:var(--panel);border-left:3px solid var(--fail);
  border-radius:6px;font-family:var(--font-mono);font-size:.82rem;
  list-style:none}
.row .findings li+li{margin-top:5px}

.chip{grid-row:1;grid-column:2;font-size:.74rem;font-weight:800;letter-spacing:.07em;
  text-transform:uppercase;padding:5px 11px;border-radius:999px;white-space:nowrap}
.chip.clear{background:var(--navy);color:#FFFFFF}
.chip.na{background:var(--muted);color:var(--slate);border:1px solid var(--line)}
.chip.fail{background:var(--fail);color:#FFFFFF}
.chip.warn{background:var(--warn-tint);color:var(--ink)}
.chip.caught{background:var(--navy);color:#FFFFFF}
.chip.missed{background:var(--fail);color:#FFFFFF}

table{border-collapse:collapse;width:100%;background:var(--panel);
  border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{background:var(--muted);color:var(--ink);text-align:left;
  font-size:.74rem;letter-spacing:.08em;text-transform:uppercase}
th,td{padding:11px 14px;border-bottom:1px solid var(--line);
  font-size:.9rem;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
td.num{font-family:var(--font-mono);text-align:right;white-space:nowrap}
.swatch{display:inline-block;padding:6px 14px;border-radius:6px;
  border:1px solid var(--line);font-size:.84rem;font-weight:600;white-space:nowrap}
.hex{font-family:var(--font-mono);font-size:.76rem;color:var(--slate);
  display:block;margin-top:4px}
.margin-bar{display:block;height:5px;border-radius:3px;background:var(--navy);
  margin-top:5px;min-width:3px}

.scope li{margin-bottom:12px;max-width:74ch}
.scope strong{display:block;font-size:.96rem}
.scope span{color:var(--slate);font-size:.9rem}

.provenance{border-top:1px solid var(--line);padding-top:20px;
  font-family:var(--font-mono);font-size:.78rem;color:var(--slate);line-height:1.9}
.provenance b{color:var(--ink);font-weight:600}
@media print{body{background:#fff}.wrap{padding:0}.row,table,.tile{break-inside:avoid}}
"""


_WORDS = {8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
          14: "fourteen", 15: "fifteen"}


def n_checks_word():
    return _WORDS.get(len(CHECKS), str(len(CHECKS)))


def render(artifact, findings, counts, negative, out_path):
    manifest = read_manifest(artifact)
    floor = manifest.get("floor", contrast.FLOOR)
    title = artifact_title(artifact)

    verdicts = {}
    for check in CHECKS:
        verdicts[check["id"]] = check_verdict(check, findings, counts)

    n_fail = sum(1 for v, _ in verdicts.values() if v == "fail")
    n_warn = sum(1 for v, _ in verdicts.values() if v == "warn")
    n_clear = sum(1 for v, _ in verdicts.values() if v == "clear")
    n_na = sum(1 for v, _ in verdicts.values() if v == "not applicable")
    n_undeclared = sum(1 for v, _ in verdicts.values() if v == "not declared")
    passing = n_fail == 0

    # ── contrast evidence, worst margin first ────────────────────────────────
    rows = []
    for p in manifest.get("text_pairings", []):
        fg, bg = p["fg"], p["bg"]
        if contrast.is_solid_hex(fg) and contrast.is_solid_hex(bg):
            r = round(contrast.ratio(fg, bg), 2)
            rows.append((p["label"], fg, bg, r, r - floor, True))
        else:
            rows.append((p["label"], fg, bg, None, None, False))
    rows.sort(key=lambda r: (r[3] is not None, r[3] if r[3] is not None else 0))

    contrast_rows = []
    for label, fg, bg, r, margin, solid in rows:
        if not solid:
            contrast_rows.append(
                f'<tr><td>{esc(label)}</td>'
                f'<td><span class="hex">{esc(fg)} on {esc(bg)}</span></td>'
                f'<td class="num">unresolved</td><td class="num">n/a</td>'
                f'<td><span class="chip fail">unresolved</span></td></tr>')
            continue
        # Bar width is capped so one very high ratio does not flatten the rest.
        width = max(0, min(100, round((margin / 10.0) * 100)))
        passed = margin >= 0
        chip = ('<span class="chip clear">pass</span>' if passed
                else '<span class="chip fail">fail</span>')
        contrast_rows.append(
            f'<tr><td>{esc(label)}</td>'
            f'<td><span class="swatch" style="color:{esc(fg)};background:{esc(bg)}">'
            f'Sample text</span>'
            f'<span class="hex">{esc(fg)} on {esc(bg)}</span></td>'
            f'<td class="num">{r}:1</td>'
            f'<td class="num">{margin:+.2f}'
            f'<span class="margin-bar" style="width:{width}%"></span></td>'
            f'<td>{chip}</td></tr>')

    # ── check ledger ─────────────────────────────────────────────────────────
    chip_class = {"clear": "clear", "not applicable": "na", "fail": "fail",
                  "warn": "warn", "not declared": "warn"}
    ledger = []
    for check in CHECKS:
        verdict, detail = verdicts[check["id"]]
        state = " is-fail" if verdict == "fail" else (
            " is-warn" if verdict in ("warn", "not declared") else "")
        findings_html = ""
        if detail:
            items = "".join(f"<li>{d}</li>" for d in detail)
            findings_html = f'<ul class="findings">{items}</ul>'
        ledger.append(
            f'<article class="row{state}">'
            f'<div class="name">{esc(check["name"])}'
            f'<span class="cid">{esc(check["wcag"])}</span></div>'
            f'<span class="chip {chip_class[verdict]}">{esc(verdict)}</span>'
            f'<p class="rule">{esc(check["rule"])}</p>'
            f'<div class="subjects">{esc(subject_line(check, counts))}</div>'
            f'{findings_html}</article>')

    # ── negative control ─────────────────────────────────────────────────────
    nc_rows = []
    n_caught = 0
    for check in CHECKS:
        defect, status = negative[check["id"]]
        if status == "caught":
            n_caught += 1
            chip = '<span class="chip caught">caught</span>'
        elif status == "MISSED":
            chip = '<span class="chip missed">missed</span>'
        else:
            chip = '<span class="chip na">not exercised</span>'
        nc_rows.append(
            f'<tr><td>{esc(check["name"])}<span class="hex">{esc(check["wcag"])}</span></td>'
            f'<td>{esc(defect)}</td><td>{chip}</td></tr>')

    scope = "".join(
        f"<li><strong>{s}</strong><span>{d}</span></li>" for s, d in OUT_OF_SCOPE)

    # ── needs your eyes: the warn-tier items, gathered for the human reviewer ──
    # How serious a finding is and how sure the script is of it are kept apart: a WARN is
    # a question the script could not settle, listed in one place for the reviewer.
    eyes_items = []
    for check in CHECKS:
        verdict, detail = verdicts[check["id"]]
        if verdict not in ("warn", "not declared"):
            continue
        for d in detail:
            eyes_items.append(
                f"<li><strong>{esc(check['name'])} "
                f"<span class=\"cid\">{esc(check['wcag'])}</span></strong><span>{d}</span></li>")
    eyes = "".join(eyes_items) or (
        "<li><strong>Nothing raised</strong><span>No check on this page left a question "
        "open. The standing questions below still apply to every page.</span></li>")

    stamp = datetime.now().strftime("%B %-d, %Y at %-I:%M %p")
    rel = Path(artifact).name
    subject_html = (f'    <p class="subject">{esc(rel)}</p>' if title != rel
                    else '    <p class="subject">This page has no title, so it is named by its file.</p>')

    # The report's own pairings, so the gate can audit the report itself.
    self_manifest = {
        "floor": 4.5,
        "text_pairings": [
            {"label": "report body on page", "fg": "#1A202C", "bg": "#F7F8FA"},
            {"label": "report body on panel", "fg": "#1A202C", "bg": "#FFFFFF"},
            {"label": "secondary text on page", "fg": "#4A5568", "bg": "#F7F8FA"},
            {"label": "secondary text on panel", "fg": "#4A5568", "bg": "#FFFFFF"},
            {"label": "table header", "fg": "#1A202C", "bg": "#EDF0F4"},
            {"label": "pass verdict text", "fg": "#1F3A5F", "bg": "#E8EEF6"},
            {"label": "fail verdict text", "fg": "#8B1E1E", "bg": "#FBECEC"},
            {"label": "fail row text", "fg": "#1A202C", "bg": "#FBECEC"},
            {"label": "clear chip", "fg": "#FFFFFF", "bg": "#1F3A5F"},
            {"label": "fail chip", "fg": "#FFFFFF", "bg": "#8B1E1E"},
            {"label": "warn chip", "fg": "#1A202C", "bg": "#F6E3B4"},
            {"label": "not-applicable chip", "fg": "#4A5568", "bg": "#EDF0F4"},
            {"label": "heading on page", "fg": "#1F3A5F", "bg": "#F7F8FA"},
        ],
        # Non-text elements a reader needs: the headroom bar, the borders, the focus ring.
        "ui_pairings": [
            {"label": "headroom bar on panel", "fg": "#1F3A5F", "bg": "#FFFFFF"},
            {"label": "masthead rule on page", "fg": "#B7791F", "bg": "#F7F8FA"},
            {"label": "pass verdict border", "fg": "#1F3A5F", "bg": "#E8EEF6"},
            {"label": "fail verdict border", "fg": "#8B1E1E", "bg": "#FBECEC"},
            {"label": "warn row border", "fg": "#B7791F", "bg": "#FFFFFF"},
            {"label": "focus ring on page", "fg": "#1F3A5F", "bg": "#F7F8FA"},
        ],
    }

    verdict_word = "Meets the standard" if passing else "Does not meet the standard"
    if passing:
        verdict_body = (
            f"All {n_checks_word()} checks were run against <b>{esc(rel)}</b> and none found a defect. "
            f"{n_clear} of them had something to examine; {n_na} found nothing of that kind on "
            f"the page and are reported as not applicable rather than counted as a pass.")
        if n_undeclared:
            verdict_body += (
                f" {n_undeclared} check{'s' if n_undeclared != 1 else ''} could not run at all "
                f"because the page does not declare what {'they need' if n_undeclared != 1 else 'it needs'}; "
                f"{'those are' if n_undeclared != 1 else 'that is'} marked below and unproven, not passed.")
    else:
        verdict_body = (
            f"{n_fail} of {n_checks_word()} checks found a defect in <b>{esc(rel)}</b>. Each is listed "
            f"below with the exact line it sits on. Every finding is a blocking one: the "
            f"page does not go out until they clear.")

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accessibility report: {esc(title)}</title>
<meta name="content-languages" content="en">
<style>{CSS}</style>
<script type="application/json" id="wcag-contrast-manifest">
{json.dumps(self_manifest, indent=2)}
</script>
</head>
<body>
<main class="wrap">

  <header class="masthead">
    <p class="eyebrow">Accessibility report</p>
    <h1>{esc(title)}</h1>
{subject_html}
  </header>

  <div class="verdict {'pass' if passing else 'fail'}">
    <span class="stamp">{verdict_word}</span>
    <p>{verdict_body}</p>
  </div>

  <div class="scoreboard">
    <div class="tile {'good' if passing else 'bad'}"><div class="n">{n_fail}</div>
      <div class="k">Defects found</div></div>
    <div class="tile good"><div class="n">{n_clear}</div>
      <div class="k">Checks cleared</div></div>
    <div class="tile mute"><div class="n">{n_na}</div>
      <div class="k">Not applicable</div></div>
    <div class="tile {'good' if (n_warn + n_undeclared) == 0 else 'bad'}">
      <div class="n">{n_warn + n_undeclared}</div>
      <div class="k">Needs a human look</div></div>
    <div class="tile good"><div class="n">{n_caught}/{len(CHECKS)}</div>
      <div class="k">Checks self-test</div></div>
  </div>

  <section>
    <h2>What was checked</h2>
    <p class="lede">{n_checks_word().capitalize()} rules, each one a yes-or-no question a script can answer from the
      page source. Every rule reports how many things it actually looked at. A rule
      with nothing to examine is marked not applicable, not passed.</p>
    <div class="ledger">
      {''.join(ledger)}
    </div>
  </section>

  <section>
    <h2>Color contrast, pairing by pairing</h2>
    <p class="lede">Each row shows the real text color on the real background color, its
      measured contrast ratio, and how much headroom it has over the {floor}:1 floor.
      Tightest margin first. The ratio is arithmetic from the WCAG relative-luminance
      formula, not an estimate.</p>
    <table>
      <thead><tr>
        <th scope="col">Where it appears</th>
        <th scope="col">As rendered</th>
        <th scope="col">Ratio</th>
        <th scope="col">Over floor</th>
        <th scope="col">Verdict</th>
      </tr></thead>
      <tbody>{''.join(contrast_rows) if contrast_rows
              else '<tr><td colspan="5">This page embeds no contrast manifest, so no '
                   'pairing could be verified.</td></tr>'}</tbody>
    </table>
  </section>

  <section>
    <h2>Proof the checks actually fire</h2>
    <p class="lede">A check that has never failed has not been tested. Before writing this
      report the tool made {n_checks_word()} throwaway copies of the page, put one deliberate defect
      into each, and re-ran the same checks. A check that did not catch its own planted
      defect would be reported here as missed. A check marked not exercised could not take its
      planted defect, usually because the page already has that defect. This ran just now,
      against this file. It is not a record of an earlier test.</p>
    <table>
      <thead><tr>
        <th scope="col">Check</th>
        <th scope="col">Defect planted in a throwaway copy</th>
        <th scope="col">Result</th>
      </tr></thead>
      <tbody>{''.join(nc_rows)}</tbody>
    </table>
  </section>

  <section class="scope">
    <h2>What needs your eyes</h2>
    <p class="lede">Two axes, kept apart: how serious a finding is, and how sure the tool is
      of it. Every verdict in the ledger above was settled by the script. The items here were
      not: each is a question the script raised but cannot answer. A finding that rests on a
      script fact plus a human call takes the human's grade, never the script's, so nothing
      listed here is counted as passed or failed above.</p>
    <ul>{eyes}</ul>
  </section>

  <section class="scope">
    <h2>What this report does not decide</h2>
    <p class="lede">The checks answer questions with one correct answer. Judgment calls stay
      with a person, and naming them is part of the report rather than a footnote to it.</p>
    <ul>{scope}</ul>
  </section>

  <footer class="provenance">
    <b>Generated</b> {esc(stamp)}<br>
    <b>Artifact audited</b> {esc(rel)} &middot; sha256 {sha256(artifact)}<br>
    <b>Checks</b> checks/web.py &middot; sha256 {sha256(gate.__file__)}<br>
    <b>Contrast math</b> checks/contrast.py &middot; sha256 {sha256(contrast.__file__)}
      &middot; floor {floor}:1<br>
    <b>Runtime</b> Python {sys.version.split()[0]}, standard library only
  </footer>

</main>
</body>
</html>
"""
    Path(out_path).write_text(doc, encoding="utf-8")
    return passing, n_caught


def build_report(artifact, out):
    """Audit `artifact`, write the report to `out`. Returns (passing, n_caught, self_ok)."""
    findings = gate.audit_html(artifact)
    counts = census(artifact)
    workdir = tempfile.mkdtemp(prefix="a11y_negative_control_")
    try:
        negative = run_negative_control(artifact, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    passing, n_caught = render(artifact, findings, counts, negative, out)
    # The report is a web page too; hold it to the standard it reports on.
    own = gate.audit_html(out)
    self_ok = not gate._has_fail(own) and not gate._has_warn(own)
    return passing, n_caught, self_ok, negative


def main(argv):
    ap = argparse.ArgumentParser(description="Accessibility report for one HTML page.")
    ap.add_argument("artifact", help="HTML page to audit")
    ap.add_argument("-o", "--out", help="output path (default: a11y_report_<name>.html "
                    "beside the page)")
    args = ap.parse_args(argv[1:])

    artifact = Path(args.artifact)
    if not artifact.is_file():
        print(f"FAIL: no such file: {artifact}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else artifact.with_name(f"a11y_report_{artifact.stem}.html")
    out.parent.mkdir(parents=True, exist_ok=True)

    passing, n_caught, self_ok, negative = build_report(artifact, out)
    print(f"wrote {out}")
    print(f"  audited page:      {'PASS' if passing else 'FAIL'}")
    print(f"  checks self-test:  {n_caught}/{len(CHECKS)} planted defects caught")
    print(f"  report self-audit: {'clean' if self_ok else 'NOT CLEAN'}")
    missed = [c["id"] for c in CHECKS if negative[c["id"]][1] == "MISSED"]
    if missed:
        print(f"  WARNING: checks that missed their planted defect: {', '.join(missed)}")
    return 0 if self_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
