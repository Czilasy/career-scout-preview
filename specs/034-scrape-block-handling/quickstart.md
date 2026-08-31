# Quickstart: 抓取阻断正确化验证指南

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

按 spec.md 的 Success Criteria（SC-001~SC-005）与用户确认的验收口径（质询 4：假样本回归 + 真实账号冒烟，不去真实触发拦截）执行验证。

## 前置

- 测试日志/输出一律使用系统临时目录，禁止写入项目根。
- 假样本：构造脚本输出/事件文件样本，不碰真实平台拦截。

## 验证 1：脚本补捕获 → 失败行 + 退出码（SC-001 前半）

1. 聚焦测试 `uv run python -m unittest tests.test_scrape_block_classification` 覆盖：
   - `RiskControlError(code="source_verification_required")` → 失败行 `code=source_verification_required` + exit(10)
   - `LoginRequiredError` → 失败行 `code=source_login_required` + exit(1)
   - `RequestLimitExceededError` → 失败行 `code=source_request_limit_exceeded` + exit(11)
2. 断言 webui `_classify_failed_code` 解析失败行得精确账号级码（非 `source_unknown_error`）。

## 验证 2：详情批非零退出事件归类（SC-001 后半 / SC-002）

1. 聚焦测试构造：非零退出 + 事件文件含账号级码（如 `source_rate_limited`）与单条软失败码（如 `source_invalid_output`）。
2. 断言：账号级码岗位按该码标失败并推进熔断信号（不进"待确认"语义）；软失败码岗位带原因标失败；已落盘产物岗位标成功。
3. 事件文件缺失场景：回退 `_classify_failed_code`，不崩溃（SC-004）。

## 验证 3：真实账号冒烟（SC-005）

1. 源码模式启动 `uv run python webui/app.py`，跑一次正常小抓取（DAD 账号）。
2. 预期：任务正常完成，无回归（不主动触发验证码/限流）。

## 回归

- 聚焦测试：`uv run python -m unittest tests.test_scrape_block_classification`
- 后端全量：`uv run python -m unittest discover -s tests`
- 前端构建：`cd webui && npm run build`
- 卫生检查：`uv run python -m unittest tests.test_repo_hygiene`

## 已知未验证边界

- 真实验证码/限流触发的端到端路径依赖外部平台状态，按用户确认不去真实触发；以假样本回归 + 真实账号正常冒烟覆盖。
