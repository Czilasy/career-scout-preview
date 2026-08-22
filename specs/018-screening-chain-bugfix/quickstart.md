# Quickstart: 筛选链路三处 Bug 修复（018）

**Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

## 前置

- 仓库：本仓库根目录（git 仓库），Python via `uv`。
- 无需前端构建（前端零改动）；无需数据库迁移。

## 聚焦验证（唯一必跑门禁）

```powershell
uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene tests.test_screen_flow
```

- 全绿为准；测试输出/日志一律进系统临时目录，不留项目根目录。

## 手动冒烟（可选）

1. `uv run python scripts/db_info.py` 确认 live 库 env=live、最新 run 不是 828f8807（清理后复核项）。
2. 对事故抓取源点"继续"，观察：续跑消息里的判定数应为链合并后数量（如 277），粗筛跳过断点岗位，幸存者不再塌缩。
3. 构造一次 AI 端点坏格式（或依赖 mock 测试）确认不出现 internal_error。

## 数据清理（代码合入后一次性执行）

```powershell
# 1) 备份
Copy-Item "$env:USERPROFILE\.career-scout\webui\webui.db" "$env:USERPROFILE\.career-scout\webui\webui.db.bak-018"
# 2) 删除幽灵轮（uv run python - <<EOF 方式临时执行，不建脚本文件）
#    DELETE FROM screening_results WHERE run_id = '828f8807-968d-4c91-b7f8-23c863302542';
#    DELETE FROM screening_runs    WHERE id      = '828f8807-968d-4c91-b7f8-23c8633025';
# 3) 复核
uv run python scripts/db_info.py
```

- 严禁动 03fb82e1/0f0baa1b/94e2c440 三条 run 及其判定、checkpoint。

## 提交

- `uv run python -m unittest tests.test_repo_hygiene` → `git status` / `git diff --check` → Conventional Commits：`fix: 筛选链路三处修复（018）…`。
