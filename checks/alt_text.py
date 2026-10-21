"""alt_text.py: the one rule for whether a piece of alt text says anything.

Presence is not the check. Word, PowerPoint and the Python libraries that write those files
fill the alt-text field by themselves, with the image's file name or a generic word. A check
that only asks "is the field non-empty" passes a document whose every image is described as
"Picture", and a screen reader then reads "Picture" aloud.

What this cannot judge is whether real text is accurate. "Students at commencement" could be
wrong about the photo and nothing here would know. Accuracy stays a human check.

Also reads Word's "Mark as decorative" flag: an image marked decorative carries no
information and needs no alt text.
"""
import re

# The extension Word, PowerPoint and Excel write for "Mark as decorative".
DECORATIVE_NS = "http://schemas.microsoft.com/office/drawing/2017/decorative"

# A file name: "chart.png", "IMG 0421.JPEG".
FILENAME_RE = re.compile(r"^[\w .()-]+\.(png|jpe?g|gif|svg|bmp|tiff?|webp|emf|wmf|heic)$", re.I)
# The generic word a tool writes, alone or numbered: "Picture", "Image 3", "Chart1".
PLACEHOLDER_RE = re.compile(
    r"^(picture|image|graphic|chart|diagram|object|shape|figure|photo|logo)( ?\d+)?$", re.I)


def decorative(element):
    """True when this drawing element carries the decorative flag."""
    for el in element.iter(f"{{{DECORATIVE_NS}}}decorative"):
        if (el.get("val") or "1") not in ("0", "false"):
            return True
    return False


def useless_alt(alt):
    """The reason this alt text says nothing, or None when it is real text."""
    alt = (alt or "").strip()
    if FILENAME_RE.match(alt):
        return "its file name"
    if PLACEHOLDER_RE.match(alt):
        return "a placeholder a tool wrote"
    return None
