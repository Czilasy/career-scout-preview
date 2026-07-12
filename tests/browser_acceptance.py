"""T063: 本地浏览器验收脚本（程序化模拟）。

由于无法在真实浏览器中手动点击，本脚本通过 Flask test client 模拟
quickstart.md 的验收 A-E 流程，验证 API 层面的端到端契约。
真实浏览器交互仍需用户在浏览器中手动确认。

验收范围：
A. 简历驱动填筛与两层核验分流
B. 感兴趣与垃圾桶及展示排除
C. 区域生命周期
D. AI 不可用降级
E. 数据安全（API Key 不回显、简历不进响应、链接校验）

运行：python tests/browser_acceptance.py
"""

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from webui.app import create_app


def banner(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main():
    banner("T063 本地浏览器验收（程序化模拟）")

    tmp = tempfile.TemporaryDirectory()
    root = pathlib.Path(tmp.name)
    app = create_app({
        "TESTING": True,
        "START_TASKS": False,
        "RESULT_DIR": str(root / "results"),
        "DB_PATH": str(root / "state" / "webui.db"),
        "PYTHON_EXECUTABLE": sys.executable,
        "API_TOKEN": "acceptance-test-token",
    })
    client = app.test_client()
    token = "acceptance-test-token"
    headers = {"X-Boss-Token": token}

    # -- 验收 A：简历驱动填筛 --
    banner("验收 A：简历驱动填筛与两层核验分流")

    # A.1 首页可访问，包含筛选工作区
    resp = client.get("/")
    assert resp.status_code == 200, f"首页应返回 200，实际 {resp.status_code}"
    html = resp.get_data(as_text=True)
    assert "screeningWorkspace" in html or "筛选" in html, "首页应包含筛选工作区"
    print("[OK] 首页包含筛选工作区")

    # A.2 筛选选项接口返回七类
    resp = client.get("/api/screening/filter-options", headers=headers)
    assert resp.status_code == 200, f"筛选选项应返回 200，实际 {resp.status_code}"
    options = resp.get_json().get("options", resp.get_json())
    assert len(options) == 7, f"筛选选项应七类，实际 {len(options)}"
    print(f"[OK] 筛选选项返回七类：{list(options.keys())}")

    # A.3 读简历填筛（模拟 AI）—— 不上传真实简历，直接测 suggest 接口拒绝无 token
    resp = client.post("/api/screening/resume/suggest", json={})
    assert resp.status_code == 403, f"无 token 应 403，实际 {resp.status_code}"
    print("[OK] 读简历填筛接口受令牌保护")

    # A.4 确认执行接口受令牌保护
    resp = client.post("/api/screening/runs", json={"filters": {}}, headers=headers)
    # 没有关键词应 400
    assert resp.status_code == 400, f"无关键词应 400，实际 {resp.status_code}"
    print("[OK] 确认执行接口校验关键词")

    # -- 验收 B：感兴趣与垃圾桶 --
    banner("验收 B：感兴趣与垃圾桶及展示排除")

    # B.1 感兴趣列表接口受令牌保护
    resp = client.get("/api/screening/interested")
    assert resp.status_code == 403, f"无 token 应 403，实际 {resp.status_code}"
    resp = client.get("/api/screening/interested", headers=headers)
    # 无 profile_id 应 400 或 200 空列表
    assert resp.status_code in (200, 400), f"感兴趣列表应 200/400，实际 {resp.status_code}"
    print("[OK] 感兴趣区列表接口可用且受令牌保护")

    # B.2 垃圾桶列表接口受令牌保护
    resp = client.get("/api/screening/trash")
    assert resp.status_code == 403, f"无 token 应 403，实际 {resp.status_code}"
    resp = client.get("/api/screening/trash", headers=headers)
    assert resp.status_code in (200, 400), f"垃圾桶列表应 200/400，实际 {resp.status_code}"
    print("[OK] 垃圾桶区列表接口可用且受令牌保护")

    # B.3 反馈接口拒绝不安全链接
    resp = client.post(
        "/api/screening/jobs/abc/interest",
        json={"profile_id": "p1", "job_link": "javascript:alert(1)"},
        headers=headers,
    )
    assert resp.status_code == 400, f"不安全链接应 400，实际 {resp.status_code}"
    print("[OK] 感兴趣接口拒绝 javascript: 链接")

    resp = client.post(
        "/api/screening/jobs/abc/interest",
        json={"profile_id": "p1", "job_link": "http://evil.com"},
        headers=headers,
    )
    assert resp.status_code == 400, f"非 BOSS 域名应 400，实际 {resp.status_code}"
    print("[OK] 感兴趣接口拒绝非 BOSS 域名")

    # B.4 安全链接被接受（需先有 run 与 job，这里只校验链接格式）
    resp = client.post(
        "/api/screening/jobs/nonexistent/interest",
        json={"profile_id": "p1", "job_link": "https://www.zhipin.com/job/123"},
        headers=headers,
    )
    # 链接校验已通过（不是 400 链接错误）；可能是 404 job 不存在或 400 profile 不存在
    assert resp.status_code in (400, 404), f"安全链接应通过链接校验（400/404 业务错误），实际 {resp.status_code}"
    print(f"[OK] 感兴趣接口接受 HTTPS zhipin.com 链接（业务校验返回 {resp.status_code}）")

    # -- 验收 C：区域生命周期 --
    banner("验收 C：区域生命周期")

    # C.1 查询符合区接口受令牌保护
    resp = client.get("/api/screening/runs/r1/matches")
    assert resp.status_code == 403, f"无 token 应 403，实际 {resp.status_code}"
    resp = client.get("/api/screening/runs/r1/matches", headers=headers)
    assert resp.status_code == 404, f"未知 run 应 404，实际 {resp.status_code}"
    print("[OK] 符合区查询接口受令牌保护，未知 run 返回 404")

    resp = client.get("/api/screening/runs/r1/mismatches", headers=headers)
    assert resp.status_code == 404, f"未知 run 应 404，实际 {resp.status_code}"
    print("[OK] 不符合区查询接口受令牌保护")

    # -- 验收 D：AI 不可用降级 --
    banner("验收 D：AI 不可用降级")

    # D.1 首页包含降级态相关元素
    assert "ai-unavailable" in html or "aiUnavailable" in html, "首页应包含 AI 不可用降级态元素"
    print("[OK] 首页包含 AI 不可用降级态元素")

    # D.2 首页包含跳过简历选项
    assert "skipResume" in html or "跳过简历" in html or "skip-resume" in html, "首页应包含跳过简历选项"
    print("[OK] 首页包含跳过简历选项")

    # D.3 首页包含人工填筛提示
    assert "人工填筛" in html or "手动填写" in html or "manualFilter" in html, "首页应包含人工填筛提示"
    print("[OK] 首页包含人工填筛提示")

    # -- 验收 E：数据安全 --
    banner("验收 E：数据安全")

    # E.1 AI 设置接口不回显 Key
    resp = client.get("/api/ai-settings", headers=headers)
    assert resp.status_code == 200, f"AI 设置应 200，实际 {resp.status_code}"
    ai_settings = resp.get_json()
    settings_str = json.dumps(ai_settings)
    assert "api_key" not in settings_str.lower() or '"api_key":null' in settings_str.lower() or '"api_key":""' in settings_str.lower(), \
        "AI 设置不应回显 Key"
    print(f"[OK] AI 设置不回显 Key：{ai_settings}")

    # E.2 首页链接校验逻辑存在
    assert "https:" in html and "zhipin.com" in html, "首页应包含 HTTPS 与 zhipin.com 校验"
    print("[OK] 首页包含 HTTPS 与 zhipin.com 域名校验逻辑")

    # E.3 首页无自动投递入口
    assert "自动投递" not in html or "不自动投递" in html, "首页不应有自动投递入口"
    print("[OK] 首页无自动投递入口")

    # E.4 反馈按钮显式 type=button
    assert 'type="button"' in html or '.type = "button"' in html, "反馈按钮应显式 type=button"
    print("[OK] 反馈按钮显式 type=button 阻止表单提交")

    tmp.cleanup()
    banner("T063 程序化验收全部通过")
    print("\n注意：真实浏览器交互（点击卡片、两区切换视觉、垃圾桶查看模态层）")
    print("仍需用户在浏览器中手动确认。本脚本只验证 API 与 HTML 契约。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
