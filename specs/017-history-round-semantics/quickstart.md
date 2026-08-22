# Quickstart: 历史轮次与流程终结语义修复

**Date**: 2026-08-22 | 验证指南（不含实现细节）

## 前置

- `uv run python -m unittest` 基线可跑；前端 `npm test` 基线可跑（webui/ 下）。
- 测试库与正式库隔离（正式库在 `~/.career-scout/webui/webui.db`，验证一律用临时库）。

## 端到端验证场景

### 场景 1：四种中断不成轮（SC-001）
1. 启动一轮抓取 + AI 筛选，产出岗位后分别验证：
   - 点暂停 → `GET /api/result-history` 轮数为 0；
   - 点继续恢复后让其出错强停（或模拟硬阻断）→ 轮数为 0；
   - 重新跑一轮并点取消 → 轮数为 0；
   - 再跑一轮中途强杀进程重启 → 轮数为 0。
2. 每种中断后任务态正确（暂停可继续、取消终结、中断可结束保存）。

### 场景 2：三种出口各恰一轮（SC-002）
1. 自然跑完 → 历史恰 1 条；再点结束保存 → 409，仍 1 条。
2. 暂停 → 结束保存 → 历史恰 1 条（无暂停残影轮）。
3. 暂停 → 继续 → 跑完 → 历史恰 1 条。
4. 跳过筛选直接看 → 历史恰 1 条 `scraped_only` 轮；对该轮补筛 → 仍 1 条，状态变 done/partial，排序位置不变。

### 场景 3：重抓原地更新 + 定稿时间（SC-004）
1. 打开含待确认岗位的结果页，发起重抓（请求必须带目标轮）。
2. 重抓完成后刷新历史：轮总数不变、该轮计数更新、显示时间为重抓完成时刻。
3. 重抓请求不带目标轮 → 409。

### 场景 4：升级清空（SC-005）
1. 用带存量历史轮的旧库启动升级版本 → 首次迁移后历史为空，活动任务不受影响。

### 场景 5：一套话术（SC-006）
1. 构造 paused / completed_with_pending / failed 任务，分别从任务列表、详情、轮询接口读状态 → 同一任务状态词三处一致，全程不出现 `waiting`。

## 测试命令

- 后端聚焦：`uv run python -m unittest tests.test_result_rounds tests.test_result_history`
- 后端全量：`uv run python -m unittest`
- 前端：`cd webui && npm test`
- 构建：`cd webui && npm run build`
- 卫生：`uv run python -m unittest tests.test_repo_hygiene`
