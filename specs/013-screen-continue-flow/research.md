# Research: AI 筛选停止/继续/恢复链路统一

**Created**: 2026-08-15
**Feature**: [spec.md](spec.md)

## Decision 1: 暂停使用新暂停请求，不复用“取消”或“结束保存”

- **Decision**: 新增 `POST /api/task/pause/<run_id>`；内存任务带 `stop_mode="pause"`，worker 在安全边界进入暂停而不是取消。
- **Rationale**: 取消是终态，结束保存是终态快照；用户暂停要求“保留断点 + 04 可看结果 + 可续跑”，只有 paused 状态与现有 `/api/task/continue` 语义完全吻合。
- **Alternatives considered**: 复用 `/api/ai-screen/<task_id>/cancel` 并把 cancelled 映射成可续跑，会破坏取消终态语义；复用 `/api/task/finish` 后再改回 paused，需要处理 worker 的 user_finished 覆盖问题，更绕。

## Decision 2: 暂停时保存“部分结果快照”，原 run 保持 paused

- **Decision**: 暂停时用现有部分结果构建逻辑生成 `result_snapshot`（status=`partial`）供 04 展示；原 screening run 状态写为 `paused`，断点、verdicts、checkpoints、JD 文件全部保留。
- **Rationale**: 04 现有加载路径只认 `result_snapshot`；快照与断点双轨并存，互不覆盖，既满足“立即可看”又满足“可续跑”。
- **Alternatives considered**: 让 `latest-pipeline-result` 直接读取 paused run，需要把结果构建逻辑暴露给查询路径且要处理内存/DB 双源，改动面更大。

## Decision 3: 续跑候选覆盖 paused/failed/interrupted(user_finished)/partial

- **Decision**: `/api/ai-screen` 查找上一轮续跑候选时，不再只认 paused 和 restart interrupted；按最新时间在 `paused`、`failed`、`interrupted(user_finished)`、`partial` 中查找，字段/画像/事实一致才续跑。
- **Rationale**: 用户已确认“结束保存后也能继续”“失败也走继续”；现有 `_run_ai_screen_task(resume_from_run_id=...)` 已经支持加载旧 verdicts/JD/checkpoint，只需扩展候选状态。
- **Alternatives considered**: 修改 DB 状态机允许 failed/partial/interrupted 直接转 running，会扩大状态机风险；当前“新 run 接管旧断点”的机制已存在，优先复用。

## Decision 4: 续跑 JD 断点缺失时从结果快照回退

- **Decision**: 新 store mixin 提供 `load_screening_jd_map(run_id)`；续跑加载 `resume_jd` 时，先读 JD checkpoint 文件，缺失时从 `screening_results` 回退。
- **Rationale**: 自然完成态会在收口时删除 JD checkpoint；如果该轮是 `partial`，JD 仍保存在 `screening_results`，不读取会导致续跑重新抓 JD。
- **Alternatives considered**: 保留 JD checkpoint 直到历史轮过期，会改变现有清理语义且增加磁盘残留。

## Decision 5: 完整本轮上下文由后端统一返回

- **Decision**: 后端从父抓取 run 的 `execution_params.script_params` 取关键词/城市，从 AI run 的 `frozen_filters` 取六类条件，连同画像/画像事实/来源任务组成 `round_context`；`latest-running-task` 与 `latest-pipeline-result` 都透传。
- **Rationale**: 关键词/城市本来就在父 run，六类条件和画像本来就在 AI run，只是没有合并回传；统一字段后前端一次恢复，不再各自拼。
- **Alternatives considered**: 前端分别调多个接口再拼，恢复路径更散，且刷新后仍会漏字段。

## Decision 6: 前端用 pure module + composable + component 替换散落按钮

- **Decision**: 新增 `screenFlow.ts`（纯状态派生）、`useScreenRoundFlow.ts`（动作与恢复）、`ScreenRoundActions.vue`（03/04 按钮组）；`DiscoveryView.vue` 只接线，净减少行数。
- **Rationale**: `DiscoveryView.vue` 已超宪法尺寸；统一主动作和恢复逻辑放新文件后，可独立测试且不再追加业务代码到大文件。
- **Alternatives considered**: 继续在 DiscoveryView 内加 computed/v-if，改动行数多且违背宪法文件边界。
