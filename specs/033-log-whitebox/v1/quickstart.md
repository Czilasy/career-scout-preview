# Quickstart: 日志白箱验证指南

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

按 spec.md 的 Success Criteria（SC-001~SC-005）与验收门禁执行验证。全部验证为真实运行，不用 mock。

## 前置

- 环境：正式库 `env=live`（`uv run python scripts/db_info.py` 确认）；DAD 账号已配置（测试期角色已设好）。
- 测试日志一律使用系统临时目录，禁止写入项目根。

## 验证 1：子进程现场进日志（SC-001）

1. 源码模式启动：`uv run python webui/app.py`（非测试模式，确保 `configure_logging()` 正常）。
2. 发起一次真实小抓取（平衡档，DAD 账号，AI+Python+东莞，如基线 run 同配置）。
3. 任务完成后检查 `~/.career-scout/logs/career-scout.log`：
   - 存在子进程现场行：详情页结果（`detail ... status=... safe_code=...`）、登录检查、风控判定；
   - 行含 `task=<任务编号>` 与时间戳。
4. 预期：三条检查全部命中。

## 验证 2：失败路径留痕（SC-002）

1. 制造一次失败（二选一）：
   - 测试桩：单测覆盖 in-process 通用异常路径（`except Exception`），断言 `career-scout.log` 出现带异常类型的记录；
   - 真实：复用已知失败条件（如断网/停掉调试浏览器）触发一次失败。
2. 检查日志：能查到异常类型、发生阶段、任务编号，而非仅"抓取执行失败"。
3. 预期：命中。

## 验证 3：卫生测试新检查（SC-003）

1. 全量卫生检查：`uv run python -m unittest tests.test_repo_hygiene` → 全绿。
2. 负向用例（临时验证后还原）：在任一业务模块加一行 `logging.getLogger(...)` 或一个"except 后仅局部赋值"的块，再跑卫生检查 → 必须失败并指出文件位置。
3. 预期：正反向均符合。

## 验证 4：日志页可查（SC-004）

1. 前端打开日志页（`/api/logs`），确认能查到验证 1/2 写入的日志行（含轮转后文件身份切换正常）。
2. 预期：命中。

## 验证 5：异常场景不崩（SC-005）

1. 抓取运行中手动删除 `career-scout.log` → 程序不崩溃，日志重建继续写。
2. 将日志目录设为只读 → 程序不崩溃，行为可感知（日志行可见降级或写失败有迹）。
3. 预期：不崩溃。

## 验证 6：子进程失败现场进主日志（FR-010 / SC-006，审查返修后补）

1. 自动化：跑 `uv run python -m unittest tests.test_logging_whitebox`，其中
   `test_subprocess_failure_field_reaches_main_log`（列表）与
   `test_batch_detail_subprocess_failure_reaches_main_log`（批量详情）构造子进程
   非 0 退出，断言主日志出现「抓取子进程异常退出」且含子进程 stderr 原文
   （测试桩，非真实抓取）。
2. 真实（可选）：触发一次真实抓取失败后打开日志页，除错误码与对外文案外，
   还应能读到子进程侧的异常类型与消息原文。
3. 预期：自动化用例命中；真实场景可查到原文而非只有文案。

## 验证 7：脚本主入口兜底未识别异常（FR-011，审查返修后补）

1. 自动化：无自动化覆盖（`__main__` 分支不便单测）。已验证
   `emit_failure_line("source_unknown_error", ...)` 的失败行能被主进程正确解析。
2. 真实：待触发一次非已知类型的抓取异常后核对日志；本轮尚无真实运行证据。

## 回归

- 后端全量：`uv run python -m unittest discover -s tests`
- 前端构建：`cd webui && npm run build`
- 卫生检查：见验证 3

## 已知未验证边界

- 真实风控/验证码触发的端到端路径依赖外部平台状态，本次仅验证日志落盘机制（验证码识别→暂停属 Spec A，不在本功能）。
- macOS 平台日志行为未实测（本项目主开发在 Windows；SafeRotatingFileHandler 的 POSIX 分支有单测覆盖）。
- 审查返修阶段仍未做真实抓取演练：子进程现场留痕与脚本兜底均有自动化覆盖，但缺少真实运行证据（用户当期无法模拟验证码场景）。
- Windows 下"日志文件被外部删除后自动重建"无自动化覆盖（该用例仅在非 Windows 平台执行）。
- 拆分已解除红线约束：`source_boss_cdp_detail.py` 由 800 行拆至 567 行，批量详情整体退出留痕已补并自动化覆盖。
- 测试噪音治理已验证：跑任意测试后，正式日志目录零新增；测试子进程日志落在系统临时目录 `%TEMP%/career-scout-test-logs`。
