# Implementation Plan: P1 完成度与界面可信度整修

**Feature Directory**: `specs/006-p1-completion` | **Date**: 2026-08-09 | **Spec**: [spec.md](./spec.md)

**Input**: 用户已确认的 P1 范围与验收口径：B011、B012、B016、B017、B020。

## Summary

本计划把 P1 五条整合为一次交付：进度条改为真实完成量驱动（B011），任务阶段不再泄漏英文原始字段（B012），应用内更新错误只显示可读中文（B016），AI 设置错误只显示可读中文（B017），主 README 补齐桌面版首次运行指引（B020），并修正 BACKLOG 的 P1 计数。

## Technical Context

**Language/Version**: Python 3.10+（后端）、TypeScript/Vue 3（前端）

**Primary Dependencies**: Flask、Vue 3、Vite、Vitest；无新增第三方依赖

**Storage**: SQLite / 本地状态文件，进度与错误状态沿用现有持久化路径

**Testing**: 后端 `uv run python -m unittest`；前端 `npm test`（Vitest）；卫生门禁 `tests.test_repo_hygiene`

**Target Platform**: Web 工作台与 Windows/macOS 桌面版共用同一前端；文档面向桌面版普通用户

**Project Type**: 本地 Web 应用 + 桌面壳

**Performance Goals**: 进度轮询与动画不引入额外网络请求或明显卡顿；前端动画仅由真实事件触发

**Constraints**:
- 进度百分比只能由真实完成事件推进；显示值不得超前于真实锚点。
- 用户可见错误只出现稳定中文；原始异常只进日志。
- 不修改抓取策略、AI 算法、更新下载协议、桌面壳与打包配置。
- 不新建下载页模板（B021 不在 P1 范围）。

**Scale/Scope**: 5 条 P1 修复，跨后端、前端、测试与文档；不进入实现。

## Constitution Check

- 公开仓库卫生：只修改项目内代码、测试与本地文档，不引入密钥、绝对路径或临时产物。
- 文档同步：README 与版本引用随改动更新。
- 不伪造完成：所有验收以真实事件、真实状态与测试证据为准。
- 不覆盖用户既有决定：B011/B012/B016/B017/B020 的边界已按用户确认写入 spec.md。
- 无对外副作用：本计划只生成设计工件，不包含仓库同步动作。

## Project Structure

```text
webui/
├── app.py                        # 任务状态、进度、更新、AI 设置接口
├── pipeline_exec.py              # 抓取进度百分比
├── updater.py                    # 更新下载/校验错误状态
├── src/components/
│   ├── TaskProgress.vue          # 进度面板与阶段标签
│   ├── UpdateDialog.vue          # 更新失败文案
│   └── AiSettingsDialog.vue      # AI 设置失败文案
├── src/components/__tests__/     # 前端组件测试
└── src/views/__tests__/          # 视图测试

README.md                         # 桌面版首启指引
roadmap/BACKLOG.md                # P1 计数与状态（本地）
specs/006-p1-completion/          # 本 Spec/Plan/Tasks
tests/                            # 后端测试
```

## Design Decisions

### D1 进度权威单一化

后端在每个进度事件中输出唯一权威 `overall_percent`；前端不再维护第二套阶段权重、预估时长或随机停顿。`/api/task-state` 的兜底 `overall_percent` 也必须使用同一真实语义，避免两套数值打架。前端只做“向真实锚点平滑追赶”的显示动画，任何时刻不超前。

### D2 抓取进度按组合均分

- 准备阶段（浏览器检测/登录检查）最多象征 1%。
- 每个组合完成时，百分比按 `已完成组合数 / 总组合数 * 100` 推进。
- `searching / waiting / combo_failed / risk_warning / closing_chrome` 不推进百分比。
- 组内优先使用真实子事件（页、条）；当前平台拿不到真实子事件时，组完成前保持不涨，状态区显示真实信息。本轮不新增平台逐页/逐条回调能力；实现阶段在基线任务中确认后记录为已知限制。

### D3 筛选与重抓按真实条数推进

筛选任务按“初筛 25% → 抓 JD 50% → 精筛 25%”三阶段权重推进，阶段内只按真实完成条数插值；重抓任务按“重抓 JD → AI 重新判定”真实完成量推进。暂停定格、失败/取消显示当前真实值或 0、完成 100%、恢复从真实断点继续。

### D4 阶段标签防泄漏

- `TaskProgress.vue` 为已知内部阶段补齐中文标签，未知阶段使用中文兜底或隐藏。
- `/api/latest-running-task` 的内存任务分支补齐 `stage`，避免刷新接回时前端回退到原始进度阶段。

### D5 更新错误稳定化

`updater.py` 与 `/api/update-restart` 只保存稳定错误码；原始异常通过日志记录。`UpdateDialog.vue` 将稳定错误码映射为中文原因与动作。

### D6 AI 错误后端统一中文化

`/api/ai-settings/test` 与 `/api/ai-settings/models` 失败时返回后端中文 `user_message`；未知错误兜底必须是纯中文，不得包含原始 `error_code`（需同步修正 `ai.py` 的 `user_facing_error` 兜底文案或在前端做纯中文兜底）。前端只展示中文文案，不再拼原始 `error_code`/`warning_codes`。

### D7 文档补全而非重写

主 README 桌面版章节补齐 Chrome/Edge、WebView2、首次启动解压延迟、macOS Gatekeeper、数据目录与常见排错，并修正版本引用；`packaging/README.md` 只做交叉引用，不重复用户指引。

## Test Strategy

- 后端：新增/更新 `tests/test_healthy_pipeline.py`、`tests/test_webui_app.py`、`tests/test_updater.py`、`tests/test_workbench_api.py`。
- 前端：重写 `TaskContinue.spec.ts` 中假进度用例，扩展 `RecrawlContinue.spec.ts`、`DiscoveryView.spec.ts`；新增 `UpdateDialog.spec.ts`、`AiSettingsDialog.spec.ts`。
- 文档：扩展 `tests/test_chrome_setup.py` 中的 README 校验。
- 最终：全量后端 unittest、全量前端 vitest、`npm run build`、`tests.test_repo_hygiene`。

## Deliverables

- `specs/006-p1-completion/spec.md`
- `specs/006-p1-completion/checklists/requirements.md`
- `specs/006-p1-completion/plan.md`
- `specs/006-p1-completion/tasks.md`

按用户“不要搞其他复杂”的要求，不生成 research.md、data-model.md、contracts/、quickstart.md。
