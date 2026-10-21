"""checks/report.py: the report is built, passes the checks it reports on, counts what each
check examined, and its planted defects are caught on both sample pages."""
from pathlib import Path

from checks import report

SAMPLE = Path(__file__).resolve().parent.parent / "sample"


def test_before_page_report(tmp_path):
    out = tmp_path / "report.html"
    passing, n_caught, self_ok, negative = report.build_report(
        SAMPLE / "announcement_before.html", out)
    assert passing is False
    assert self_ok, "the report must pass the same checks it reports on"
    statuses = {cid: status for cid, (_defect, status) in negative.items()}
    assert "MISSED" not in statuses.values()
    # The page already has no lang attribute, so that defect cannot be planted again.
    assert statuses["lang"] == "not exercised"
    assert n_caught == len(report.CHECKS) - 1
    html = out.read_text()
    assert "Does not meet the standard" in html
    assert "RSVP deadline callout: #B7791F on #FFFFFF = 3.64:1" in html


def test_after_page_report_catches_every_planted_defect(tmp_path):
    out = tmp_path / "report.html"
    passing, n_caught, self_ok, _ = report.build_report(SAMPLE / "announcement_after.html", out)
    assert passing is True
    assert self_ok
    assert n_caught == len(report.CHECKS)


def test_nothing_to_examine_reads_not_applicable(tmp_path):
    page = tmp_path / "plain.html"
    page.write_text('<!DOCTYPE html><html lang="en"><head><title>Plain</title></head>'
                    '<body><main><h1>Plain</h1><p>No images here.</p></main></body></html>')
    findings = report.gate.audit_html(page)
    counts = report.census(page)
    by_id = {c["id"]: c for c in report.CHECKS}
    assert report.check_verdict(by_id["alt"], findings, counts)[0] == "not applicable"
    # Every page has text on a background, so no declared pairings is unproven, not clean.
    assert report.check_verdict(by_id["contrast"], findings, counts)[0] == "not declared"


def test_report_escapes_page_supplied_text(tmp_path):
    page = tmp_path / "hostile.html"
    page.write_text(
        '<!DOCTYPE html><html lang="en"><head><title>T</title>'
        '<script type="application/json" id="wcag-contrast-manifest">'
        '{"text_pairings":[{"label":"<b>x</b>","fg":"#CCCCCC","bg":"#FFFFFF"}]}</script>'
        '</head><body><main><h1>T</h1></main></body></html>')
    out = tmp_path / "r.html"
    report.build_report(page, out)
    assert "<b>x</b>" not in out.read_text()
