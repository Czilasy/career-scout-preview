"""Static contract for the isolated phase 2/3 screening prototype.

The prototype intentionally has no production API or database dependency.
"""

from pathlib import Path


PROTOTYPE = Path(__file__).parents[1] / "webui" / "screening-prototype.html"


def test_screening_prototype_exposes_required_simulated_operations():
    html = PROTOTYPE.read_text(encoding="utf-8")

    for text in ("符合", "不符合", "待核验", "感兴趣", "垃圾桶"):
        assert text in html

    for action in ("toggleInterest", "moveToTrash", "restoreJob", "retryJob", "retryAll", "manualRoute"):
        assert action in html

    assert "sessionStorage" in html
    assert "prefers-reduced-motion" in html
    assert "@media (max-width: 720px)" in html
    assert "AI 核验超时" in html
    assert "字段解析失败" in html


def test_screening_prototype_is_explicitly_mock_only():
    html = PROTOTYPE.read_text(encoding="utf-8")

    assert "模拟数据" in html
    assert "不连接真实后端" in html
    assert "fetch(" not in html
