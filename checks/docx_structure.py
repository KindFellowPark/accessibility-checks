"""docx_structure.py: the structural promises a screen reader depends on in a Word file.

Five checks, each one a yes-or-no question a script can answer from the file itself:

  1. Heading order (WCAG 1.3.1). The first heading is Heading 1 and no level is skipped
     going down (Heading 1 straight to Heading 3 fails).
  2. Visual headings (WCAG 1.3.1). A short paragraph that is all bold and stands over body
     text, with no Heading style, fails. It looks like a heading, but a screen reader reads
     it as one more sentence and the document outline skips it. A bold label inside a
     sentence, a bold table cell, or a bold closing line passes.
  3. Table header rows (WCAG 1.3.1). Every table's first row is marked as a header row, so
     a screen reader can announce column names and the row repeats across pages.
  4. Document language (WCAG 3.1.1). A default language is declared.
  5. Document title (WCAG 2.4.2). The file's Title property is set. It is what a screen
     reader announces and what a PDF exported from the file shows in its title bar.

These are necessary conditions, not the whole of accessibility: a sound outline does not
mean the prose under each heading makes sense. Standard library only.

    python3 -m checks.docx_structure FILE.docx [FILE2.docx ...]

Exit 0 = every check passes. Exit 1 = at least one fails. Exit 2 = bad invocation.
"""
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"


def _q(tag):
    """Qualify a wordprocessingml tag name."""
    return f"{{{W_NS}}}{tag}"


# Styles that are real document parts but must NEVER be counted as outline
# headings. Compared case-insensitively with spaces stripped, and TOC* matched
# by prefix, so "Heading 1"/"Heading1", "TOC Heading"/"TOC 1" all normalize.
_IGNORE_EXACT = {
    "title",
    "subtitle",
    "caption",
    "header",
    "footer",
    "normal",
    "bodytext",
    "body",
    "nospacing",
}
_IGNORE_PREFIX = ("toc",)

# Matches a heading style id/name and captures the level. Word stores either
# "Heading 1" / "heading 1" (display name) or "Heading1" (style id); both reduce
# to the same normalized form below, so one regex handles both.
_HEADING_RE = re.compile(r"^heading\s*([1-9])$")


def _norm(name):
    """Lowercase and strip spaces for robust, case-insensitive style matching."""
    return (name or "").strip().lower().replace(" ", "")


def _build_style_index(z):
    """Map styleId -> (normalized_name, normalized_id) from styles.xml.

    document.xml references styles by w:val, which is the styleId. The
    human-facing name lives in w:name. We resolve a paragraph's style through
    either spelling, so a heading is detected whether the file uses the id
    'Heading1' or a name 'heading 1'.
    """
    index = {}
    try:
        styles_xml = z.read("word/styles.xml")
    except KeyError:
        return index
    root = ET.fromstring(styles_xml)
    for style in root.iter(_q("style")):
        style_id = style.get(_q("styleId"))
        if style_id is None:
            continue
        name_el = style.find(_q("name"))
        name_val = name_el.get(_q("val")) if name_el is not None else None
        index[style_id] = (_norm(name_val), _norm(style_id))
    return index


def _heading_level(norm_name, norm_id):
    """Return the heading level (1-9) for a style, or None if it is not an
    outline heading. Ignore-set styles are explicitly returned as None so a
    Title or TOC entry never counts as a heading."""
    for token in (norm_name, norm_id):
        if not token:
            continue
        if token in _IGNORE_EXACT or token.startswith(_IGNORE_PREFIX):
            return None
    for token in (norm_name, norm_id):
        m = _HEADING_RE.match(token or "")
        if m:
            return int(m.group(1))
    return None


def _paragraph_style_id(paragraph):
    """Return the styleId referenced by a paragraph's pPr/pStyle, or None."""
    ppr = paragraph.find(_q("pPr"))
    if ppr is None:
        return None
    pstyle = ppr.find(_q("pStyle"))
    if pstyle is None:
        return None
    return pstyle.get(_q("val"))


def check_heading_order(body, style_index):
    """Read paragraph styles in document order and validate the outline.

    Rules: the first heading encountered must be Heading 1; no jump of more than
    one level is allowed (Heading 1 -> Heading 3 is a skip and fails). Returns a
    list of failure strings (empty if the outline is sound).
    """
    failures = []
    levels = []
    for para in body.iter(_q("p")):
        sid = _paragraph_style_id(para)
        if sid is None:
            continue
        norm_name, norm_id = style_index.get(sid, (_norm(sid), _norm(sid)))
        level = _heading_level(norm_name, norm_id)
        if level is not None:
            levels.append(level)

    if not levels:
        return failures  # no headings is not a structural failure per se

    if levels[0] != 1:
        failures.append(
            f"heading order: first heading is Heading {levels[0]}, must be Heading 1"
        )
    prev = levels[0]
    for lvl in levels[1:]:
        if lvl - prev > 1:
            failures.append(
                f"heading order: skip from Heading {prev} to Heading {lvl} "
                f"(may not jump more than one level)"
            )
        prev = lvl
    return failures


def _bold_style_ids(styles_root):
    """Style ids whose own run properties set bold, so a run or paragraph carrying
    such a style is bold without an explicit w:b."""
    ids = set()
    if styles_root is None:
        return ids
    for style in styles_root.iter(_q("style")):
        rpr = style.find(_q("rPr"))
        if rpr is not None and _is_on(rpr.find(_q("b"))):
            sid = style.get(_q("styleId"))
            if sid:
                ids.add(sid)
    return ids


def _is_on(el):
    """A toggle property element is on when present with no val, or a val that is
    not an explicit off."""
    if el is None:
        return False
    val = (el.get(_q("val")) or "").strip().lower()
    return val not in ("0", "false", "off")


def _run_is_bold(run, bold_styles):
    rpr = run.find(_q("rPr"))
    if rpr is None:
        return False
    if _is_on(rpr.find(_q("b"))):
        return True
    rstyle = rpr.find(_q("rStyle"))
    return rstyle is not None and rstyle.get(_q("val")) in bold_styles


def _para_text_runs(para):
    """(text, runs) for a paragraph: its visible text and the runs that carry any."""
    runs = []
    for run in para.iter(_q("r")):
        text = "".join(t.text or "" for t in run.iter(_q("t")))
        if text.strip():
            runs.append((run, text))
    return "".join(t for _r, t in runs), runs


VISUAL_HEADING_MAX_WORDS = 12


def check_visual_headings(body, style_index, styles_root):
    """A body paragraph that LOOKS like a heading but is not styled as one.

    The test, all four parts required: not inside a table and not a list item;
    its paragraph style is not a heading, title, subtitle or caption; every run
    that carries text is bold (explicit w:b, a bold character style, or a bold
    paragraph style), and the text is at most VISUAL_HEADING_MAX_WORDS words; and
    the next body paragraph with text is not itself all-bold, so the bold line is
    standing over body text the way a heading does. A bold label inside a
    sentence ("Deadline: Friday" with only the label bold) is not all-bold and
    passes; a bold table cell passes; a bold closing line with nothing after it
    passes. Returns a list of failure strings.
    """
    bold_styles = _bold_style_ids(styles_root)
    in_table = set()
    for tbl in body.iter(_q("tbl")):
        for para in tbl.iter(_q("p")):
            in_table.add(id(para))

    paras = []
    for para in body.iter(_q("p")):
        if id(para) in in_table:
            continue
        ppr = para.find(_q("pPr"))
        if ppr is not None and ppr.find(_q("numPr")) is not None:
            continue
        text, runs = _para_text_runs(para)
        if not runs:
            continue
        sid = _paragraph_style_id(para)
        norm_name, norm_id = style_index.get(sid, (_norm(sid), _norm(sid))) if sid else ("", "")
        styled = _heading_level(norm_name, norm_id) is not None or norm_name in (
            "title", "subtitle", "caption") or norm_id in ("title", "subtitle", "caption")
        para_bold = sid in bold_styles
        all_bold = para_bold or all(_run_is_bold(r, bold_styles) for r, _t in runs)
        paras.append((text.strip(), styled, all_bold))

    failures = []
    for i, (text, styled, all_bold) in enumerate(paras):
        if styled or not all_bold:
            continue
        if len(text.split()) > VISUAL_HEADING_MAX_WORDS:
            continue
        following = paras[i + 1] if i + 1 < len(paras) else None
        if following is None or following[2]:
            continue
        shown = text if len(text) <= 60 else text[:57] + "..."
        failures.append(
            f'visual heading: bold body paragraph "{shown}" stands over body text with no '
            f"Heading style (a screen reader reads it as a sentence; the outline skips it)"
        )
    return failures


def check_table_headers(body):
    """Every table's first row must carry w:tblHeader in its trPr. Returns a
    list of failure strings, one per offending table."""
    failures = []
    for idx, tbl in enumerate(body.iter(_q("tbl")), start=1):
        first_row = tbl.find(_q("tr"))
        if first_row is None:
            continue  # a table with no rows has no header row to check
        trpr = first_row.find(_q("trPr"))
        has_header = trpr is not None and trpr.find(_q("tblHeader")) is not None
        if not has_header:
            failures.append(
                f"table header: table #{idx} first row is not marked as a header "
                f"row (missing w:tblHeader in trPr)"
            )
    return failures


def check_language(z, style_index_root):
    """A default document language must be declared. Accept either
    styles.xml docDefaults/rPrDefault/rPr/w:lang OR core.xml dc:language.
    Returns a list of failure strings (empty if a language is found)."""
    # Path 1: styles.xml docDefaults default run language.
    if style_index_root is not None:
        doc_defaults = style_index_root.find(_q("docDefaults"))
        if doc_defaults is not None:
            for lang in doc_defaults.iter(_q("lang")):
                val = (
                    lang.get(_q("val"))
                    or lang.get(_q("eastAsia"))
                    or lang.get(_q("bidi"))
                )
                if val and val.strip():
                    return []
    # Path 2: core properties dc:language.
    try:
        core_xml = z.read("docProps/core.xml")
    except KeyError:
        core_xml = None
    if core_xml is not None:
        core = ET.fromstring(core_xml)
        lang_el = core.find(f"{{{DC_NS}}}language")
        if lang_el is not None and (lang_el.text or "").strip():
            return []
    return [
        "language: no default document language declared "
        "(styles.xml docDefaults w:lang or core.xml dc:language)"
    ]


def check_title(z):
    """Core properties dc:title must be present and non-empty. Returns a list of
    failure strings (empty if a title is found)."""
    try:
        core_xml = z.read("docProps/core.xml")
    except KeyError:
        return ["title: docProps/core.xml is missing (no document title)"]
    core = ET.fromstring(core_xml)
    title_el = core.find(f"{{{DC_NS}}}title")
    if title_el is None or not (title_el.text or "").strip():
        return ["title: core properties dc:title is missing or empty"]
    return []


def check_docx(docx_path):
    """Run all structural checks against one docx. Returns a list of failure
    strings (empty list means the file passes)."""
    failures = []
    with zipfile.ZipFile(docx_path) as z:
        style_index = _build_style_index(z)

        # styles.xml root reused for the language check (avoid re-reading/parsing).
        try:
            styles_root = ET.fromstring(z.read("word/styles.xml"))
        except KeyError:
            styles_root = None

        try:
            doc_root = ET.fromstring(z.read("word/document.xml"))
        except KeyError:
            return ["structure: word/document.xml is missing (not a valid docx)"]
        body = doc_root.find(_q("body"))
        if body is None:
            return ["structure: document.xml has no w:body"]

        failures.extend(check_heading_order(body, style_index))
        failures.extend(check_visual_headings(body, style_index, styles_root))
        failures.extend(check_table_headers(body))
        failures.extend(check_language(z, styles_root))
        failures.extend(check_title(z))
    return failures


def main(argv):
    files = argv[1:]
    if not files:
        print(
            "usage: python3 -m checks.docx_structure FILE.docx [FILE2.docx ...]",
            file=sys.stderr,
        )
        return 2
    failed = 0
    for path in files:
        try:
            problems = check_docx(path)
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            failed += 1
            print(f"FAIL  {path}: could not read docx ({exc})")
            continue
        if problems:
            failed += 1
            print(f"FAIL  {path}: {len(problems)} structural issue(s)")
            for problem in problems:
                print(f"        - {problem}")
        else:
            print(f"OK    {path}: heading order, visual headings, table headers, language, title all pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
