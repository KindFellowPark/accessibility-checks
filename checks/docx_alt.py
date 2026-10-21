"""docx_alt.py: every image in a Word file has usable alt text (WCAG 1.1.1).

Reads the alt text Word stores on each drawing (the `descr` attribute of <wp:docPr>) in the
body, headers and footers. An image fails when its alt text is missing, empty, its file name,
or a placeholder a tool wrote ("Picture 3"), unless it is marked decorative. Whether real alt
text is accurate is a human judgment and out of scope.

Standard library only: a .docx is a zip of XML files.

    python3 -m checks.docx_alt FILE.docx [FILE2.docx ...]

Exit 0 = every image has usable alt text. Exit 1 = at least one does not. Exit 2 = bad
invocation.
"""
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

from checks.alt_text import decorative, useless_alt

WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_PART_RE = re.compile(r"word/(document|header\d*|footer\d*)\.xml$")


def count_images(docx_path):
    """How many drawings the check examines, for the report's subject count."""
    n = 0
    with zipfile.ZipFile(docx_path) as z:
        for part in (p for p in z.namelist() if _PART_RE.match(p)):
            n += sum(1 for _ in ET.fromstring(z.read(part)).iter(f"{{{WP_NS}}}docPr"))
    return n


def missing_alt(docx_path):
    """(part, docPr_id, docPr_name, why) for every drawing with no usable alt text."""
    missing = []
    with zipfile.ZipFile(docx_path) as z:
        for part in (n for n in z.namelist() if _PART_RE.match(n)):
            root = ET.fromstring(z.read(part))
            for doc_pr in root.iter(f"{{{WP_NS}}}docPr"):
                descr = (doc_pr.get("descr") or "").strip()
                if decorative(doc_pr):
                    continue
                why = "no alt text" if not descr else useless_alt(descr)
                if why:
                    missing.append((part, doc_pr.get("id"), doc_pr.get("name"), why))
    return missing


def main(argv):
    files = argv[1:]
    if not files:
        print("usage: python3 -m checks.docx_alt FILE.docx [FILE2.docx ...]", file=sys.stderr)
        return 2
    failed = 0
    for path in files:
        miss = missing_alt(path)
        if miss:
            failed += 1
            print(f"FAIL  {path}: {len(miss)} image(s) with no usable alt text")
            for part, pid, name, why in miss:
                print(f"        - {part}  image id={pid} name={name!r}: {why}")
        else:
            print(f"OK    {path}: every image has usable alt text")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
