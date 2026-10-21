"""The sample files do what the README says: each "before" file fails with the defects it was
built with, and each "after" file passes every check."""
from pathlib import Path

from checks import docx_alt, docx_contrast, docx_structure, web
from checks.__main__ import main as cli

SAMPLE = Path(__file__).resolve().parent.parent / "sample"


def test_before_page_has_its_five_defects():
    f = web.audit_html(SAMPLE / "announcement_before.html")
    assert f["alt_missing"]
    assert [c[0] for c in f["contrast_failures"]] == ["RSVP deadline callout"]
    assert f["missing_title"]
    assert f["missing_lang"]
    assert f["heading_skip"]


def test_after_page_is_clean():
    f = web.audit_html(SAMPLE / "announcement_after.html")
    assert not web._has_fail(f) and not web._has_warn(f)


def test_before_memo_has_its_defects():
    problems = docx_structure.check_docx(SAMPLE / "memo_before.docx")
    assert any(p.startswith("visual heading") for p in problems)
    assert any(p.startswith("table header") for p in problems)
    assert any(p.startswith("title") for p in problems)
    assert docx_alt.missing_alt(SAMPLE / "memo_before.docx")
    manifest = docx_contrast.load_manifest(SAMPLE / "memo_before_colors.json")
    assert docx_contrast.check_manifest(manifest)[2]


def test_after_memo_is_clean():
    assert docx_structure.check_docx(SAMPLE / "memo_after.docx") == []
    assert docx_alt.missing_alt(SAMPLE / "memo_after.docx") == []
    manifest = docx_contrast.load_manifest(SAMPLE / "memo_after_colors.json")
    assert docx_contrast.check_manifest(manifest)[2:] == ([], [])


def test_command_line_exit_codes():
    assert cli(["checks", "web", str(SAMPLE / "announcement_before.html")]) == 1
    assert cli(["checks", "web", str(SAMPLE / "announcement_after.html")]) == 0
    assert cli(["checks", "docx", str(SAMPLE / "memo_before.docx"),
                "--colors", str(SAMPLE / "memo_before_colors.json")]) == 1
    assert cli(["checks", "docx", str(SAMPLE / "memo_after.docx"),
                "--colors", str(SAMPLE / "memo_after_colors.json")]) == 0
    assert cli(["checks"]) == 2
