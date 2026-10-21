"""One command for every check.

    python3 -m checks web PAGE.html [PAGE2.html ...]
    python3 -m checks docx MEMO.docx [--colors COLORS.json]
    python3 -m checks report PAGE.html [-o OUT.html]

`web` runs the thirteen page checks. `docx` runs the Word checks (alt text and structure) on
the file and, when given the builder's declared color pairings, the contrast check on those.
`report` writes the plain-language HTML report for a page.

Exit 0 means every check passed. Anything else means the file is not done.
"""
import sys

from checks import docx_alt, docx_contrast, docx_structure, report, web

USAGE = __doc__.split("\n\n")[1]


def run_docx(args):
    colors = None
    if "--colors" in args:
        i = args.index("--colors")
        if i + 1 >= len(args):
            print("--colors needs a path", file=sys.stderr)
            return 2
        colors = args[i + 1]
        args = args[:i] + args[i + 2:]
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    codes = [docx_alt.main(["docx_alt", *args]), docx_structure.main(["docx_structure", *args])]
    if colors:
        codes.append(docx_contrast.main(["docx_contrast", colors]))
    else:
        print("NOTE  no color pairings given (--colors), so contrast is unproven, not passed")
    return max(codes)


def main(argv):
    if len(argv) < 2 or argv[1] not in ("web", "docx", "report"):
        print(USAGE, file=sys.stderr)
        return 2
    cmd, args = argv[1], argv[2:]
    if cmd == "web":
        return web.main(["web", *args])
    if cmd == "docx":
        return run_docx(args)
    return report.main(["report", *args])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
