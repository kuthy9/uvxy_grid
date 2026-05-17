# telegram_bot/tests/test_handlers_report.py
from telegram_bot.handlers import report as R


def test_report_finds_latest_weekly(tmp_path):
    a = tmp_path / "weekly_2026W19.html"
    b = tmp_path / "weekly_2026W20.html"
    a.write_text("<html>a</html>")
    b.write_text("<html>b</html>")
    out = R.handle(args=[], ctx={"report_dir": tmp_path})
    assert isinstance(out, dict)
    assert out["document"] == b
    assert "2026W20" in out["caption"]


def test_report_when_none(tmp_path):
    out = R.handle(args=[], ctx={"report_dir": tmp_path})
    assert isinstance(out, str)
    assert "no" in out.lower()


def test_report_when_dir_missing(tmp_path):
    out = R.handle(args=[], ctx={"report_dir": tmp_path / "absent"})
    assert isinstance(out, str)
    assert "no" in out.lower() or "missing" in out.lower()
