"""docx_contrast.py: every color pairing a document builder declares clears the floor
(WCAG 1.4.3).

The pairings are declared as data before the document is built, in a small JSON file:

    { "floor": 4.5,
      "text_pairings": [ { "label": "body text", "fg": "#1A202C", "bg": "#FFFFFF" } ] }

What this proves, stated plainly: the declared pairings clear the floor. It does not read
colors out of the finished Word file. The two agree because of a builder rule (see
rules/document_accessibility_rules.md): every color in the document comes from the declared
list, and no color is typed in anywhere else.

    python3 -m checks.docx_contrast COLORS.json

Exit 0 = every pairing clears. Exit 1 = a pairing fails or cannot be resolved. Exit 2 = bad
invocation or unreadable file.
"""
import json
import sys
from pathlib import Path

from checks import contrast


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_manifest(manifest):
    """(floor, pairings, failures, skipped) for one declared manifest."""
    floor = manifest.get("floor", contrast.FLOOR)
    pairings = [(p["label"], p["fg"], p["bg"]) for p in manifest.get("text_pairings", [])]
    failures, skipped = contrast.check_contrast(pairings, floor=floor)
    return floor, pairings, failures, skipped


def main(argv):
    if len(argv) != 2:
        print("usage: python3 -m checks.docx_contrast COLORS.json", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        manifest = load_manifest(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2
    floor, pairings, failures, skipped = check_manifest(manifest)
    if failures or skipped:
        print(f"FAIL  {path}")
        for label, fg, bg, r in failures:
            print(f"        contrast: {label}: {fg} on {bg} = {r}:1 (floor {floor}:1)")
        for label, fg, bg in skipped:
            print(f"        contrast: {label}: cannot be resolved, {fg} on {bg}")
        return 1
    print(f"OK    {path}: {len(pairings)} pairings clear {floor}:1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
