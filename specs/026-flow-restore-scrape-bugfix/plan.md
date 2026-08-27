# Implementation Plan: 流程恢复与抓取链路三处缺陷修复

**Branch**: `026-flow-restore-scrape-bugfix` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/026-flow-restore-scrape-bugfix/spec.md`

## Summary

修复三个独立的缺陷，均已在会话中确认根因与用户判据：

1. **B078（前端）**：启动/刷新恢复策略改为"用户上次是否进过 04 页（结果页）"为唯一判据。进过 04 页＝已结束 → 只显示 01 页，绝不恢复 02/03 页或弹"服务重启被中断"；没进过＝未结束 → 恢复半截流程续跑。
2. **B079（后端，跨模块链路）**：列表抓取结果文件 `os.replace` 偶发失败被误报"登录态失效或环境异常"。修法：①`write_json_atomic` 的 `os.replace` 加短暂重试（偶发占用重试即过）；②重试耗尽抛专门异常 `ResultFileWriteError`，子进程顶层映射为独立退出码 + 结构化失败行 `source_result_write_failed`，上游分类为"结果文件写入失败"，与登录失效严格区分——任何情况下都不再误报登录失效。
3. **B080（后端）**：重抓补抓成功的 JD 未写入 AI 精筛输入，导致永远"未抓到 JD 无法精筛"。修法：`recrawl_task.py` 装配段补 `jj["jd"] = jd`。

## Technical Context

**Language/Version**: Python 3.11 + TypeScript/Vue3（前端）

**Primary Dependencies**: Flask（WebUI）、websocket-client（CDP）、Vue3 + Vite（前端）

**Storage**: SQLite（`~/.career-scout/webui/webui.db`）、文件系统（job-result 产物）

**Testing**: Python `unittest`、前端 Vitest；`npm run build` + 仓库卫生检查

**Target Platform**: Windows 桌面 + 本地 WebUI

**Project Type**: Web 桌面应用（Python 后端 + Vue 前端）

**Performance Goals**: 不涉及（修复类）

**Constraints**: 门面文件（`app.py`/`store.py`/`source.py`/`boss_cdp_raw.py`）不追加业务逻辑；行数门禁（≤600 行预警/800 红线，Vue ≤900/1200）

**Scale/Scope**: 三处局部修复，单文件小改动

## Constitution Check

*GATE: 通过。* 依据 `.specify/memory/constitution.md`：

- **原则 I/VI（职责分层/门面禁加逻辑）**：本批只改业务域模块与 composable，`app.py`/`store.py`/`source.py` 门面不碰。`scripts/boss_cdp_raw.py` 门面仅加 1 个薄 `except ResultFileWriteError` 映射（属 CLI 入口既有"异常→退出码"职责，非业务逻辑，获用户豁免确认，见 File Boundaries 注意）。
- **原则 III（引用方向）**：后端 `runners/ai_screen_jd.py → pipeline_exec_details.py`；`scripts/boss/output.py` 内部自足（重试逻辑内聚）；前端 `view → composable → api/client`，无反向依赖。
- **原则 V（验证门禁）**：交付前通过聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 卫生检查。
- **原则 II（尺寸边界）**：改动均为小增量，目标文件不超线。

无违反项，无需 Complexity Tracking。

## File Boundaries

*GATE: 已与用户确认（2026-08-27）。*

- **Allowed files**:
  - B078：`webui/src/composables/useDiscoveryWorkflow.ts`、`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/composables/useDiscoveryTasks.ts`、`webui/src/views/DiscoveryView.vue`（仅最小组装）
  - B079（跨模块链路，已确认补全）：`scripts/boss/output.py`、`scripts/boss/exceptions.py`、`scripts/boss_cdp_raw.py`（仅加 1 个薄 `except ResultFileWriteError` 映射，非业务逻辑）、`webui/error_registry.py`（仅加 1 个 source 错误码）、`webui/source_boss_helpers.py`（补退出码文案）、`webui/src/errorCodes.ts`（镜像同步，由测试保证）
  - B080：`webui/runners/recrawl_task.py`
  - `tests/`（新增/修改测试）、`.specify/memory/constitution.md`（如涉及新文件登记，本批不新增文件则免）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`（门面，本批不追加逻辑）、`webui/store_*.py`（数据层不动）、`webui/ai_screening.py`（精筛判定本身不动）、`webui/screening_jd_gate.py`（`has_usable_jd`/`missing_jd_verdict` 判定不动，仅 B080 从上游装配传入正确 JD）
  - **注意**：`scripts/boss_cdp_raw.py` 已从 Forbidden 移到 Allowed（仅限 B079 加 1 个薄 `except ResultFileWriteError` 映射，这是 CLI 入口既有退出码映射职责，非业务逻辑；已获用户豁免确认）。
- **New files**: 无（三个修复均为既有文件定点改动，无需新模块；`ResultFileWriteError` 定义在既有 `scripts/boss/exceptions.py` 域包内）
- **Reference direction**: 后端 `runners/recrawl_task.py`（装配 JD → 传给 `ai_screening.match_jds`）；`scripts/boss/output.py`（自足重试）；前端 `DiscoveryView.vue → useDiscoveryWorkflow/useDiscoveryExecution/useDiscoveryTasks`
- **Line gate**: 目标文件均小增量，不触及 600/900 预警线
- **Rationale**: 三个修复都是对既有缺陷的定点修改，落位到既有域模块即可，无需新增文件，避免过度拆分。

## Verification Gate

- 功能/重构交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 本批不涉及版本提升/打包/发布。

## Project Structure

### Documentation (this feature)

```text
specs/026-flow-restore-scrape-bugfix/
├── spec.md              # Feature spec（已生成）
├── plan.md              # 本文件
├── tasks.md             # /speckit-tasks 输出（下一步）
└── checklists/
    └── requirements.md  # 质量检查清单
```

### Source Code（修复落点，非新增）

```text
scripts/boss/output.py            # B079: write_json_atomic os.replace 重试
webui/runners/recrawl_task.py     # B080: 精筛装配补 jj["jd"] = jd
webui/src/composables/useDiscoveryWorkflow.ts   # B078: 恢复/持久化判据
webui/src/composables/useDiscoveryExecution.ts  # B078: restoreRunningTask interrupted 分支
webui/src/composables/useDiscoveryTasks.ts      # B078: maybeAutoStartNewRound 判据
webui/src/views/DiscoveryView.vue               # B078: 最小组装
```

## 技术方案要点

### B078 · 启动/刷新恢复策略（前端）

**现状问题**：
- `useDiscoveryExecution.restoreRunningTask`（line 159）对 `data.status === "interrupted"` 无条件恢复 02/03 页 + 弹"服务重启被中断"。当用户上次已结束（进过 04 页）但后端残留历史 interrupted run 时，会误恢复。
- `useDiscoveryWorkflow.persistWorkflowState`（line 50-92）用 `workflowIsFinished()`（= `resultsPageSeen || finishedPartial`）+ 一堆任务状态 ref 计算 `unfinished`。`resultsPageSeen` 已在持久化快照里（line 87）。

**修复原则（用户判据）**：**"进没进 04 页" 是流程是否结束的唯一判据。**
- 进过 04 页（`resultsPageSeen=true` 已持久化）＝ 已结束 → 启动/刷新只显示 01 页，`restoreRunningTask` 不恢复 interrupted run、不弹提示。
- 没进过（`resultsPageSeen` 非 true）＝ 未结束 → 才允许恢复半截流程（含 interrupted 续跑）。

**具体改动**：
1. `restoreWorkflowState`（useDiscoveryWorkflow）：`resultsPageSeen` 已经是持久化字段（line 103 `resultsPageSeen.value = Boolean(saved.resultsPageSeen)`）。保存有 `unfinished:true` 但 `resultsPageSeen:false` 时＝确实未结束。**核心：持久化时 `resultsPageSeen` 必须准确反映"进没进 04 页"**。
2. `restoreRunningTask`（useDiscoveryExecution line 159 `interrupted` 分支）：进入该分支前**先判断是否"本次未进 04 页"**。若已进 04 页（已结束）→ **不恢复 interrupted run**（`interruptedRunId` 不设、不 enterScreenStep、不弹提示），保持 01 页。
   - 判据来源：恢复后的 `resultsPageSeen`（从 workflow 快照读，或"已结束"标记）。
3. `maybeAutoStartNewRound`（useDiscoveryTasks）：判定"已完成"的判据对齐为"是否进过 04 页 / 是否已结束"，而不是依赖 `has_newer_saved_result_than` 之类的时间戳推断（后端归档后失效）。
4. `DiscoveryView.vue`：初始化时序——先恢复 workflow 状态，若已结束则**不触发** `restoreRunningTask` 的 interrupted 恢复，只显示 01 页。

**实现要点**：以"持久化的已结束事实（resultsPageSeen / finishedPartial）"作为主闸门，`restoreRunningTask` 的 `interrupted`/`paused` 分支只有在未结束时才执行。已结束时，即使 `/api/latest-running-task` 返回 interrupted，也跳过恢复。

### B079 · 列表抓取文件写失败误报（后端，跨模块链路补全）

**根因**：`scripts/boss/output.py:69` `os.replace(temp_path, path)` 在 Windows 下偶发因目标文件被占用（杀软/索引/OneDrive 瞬时锁）抛 OSError → 顶层无 `except OSError` 捕获 → 作为未处理异常，子进程退出码 1 → `source_boss_helpers._classify_failed_code(1, ...)`（无高置信登录短语）→ `source_unknown_error` + `_EXIT_REASONS[1]` = "登录态失效或环境异常" 误报。

**修复（两层，确保"重试成功不误报 + 重试耗尽也不误报登录失效"）**：

**第一层（治本）**：`write_json_atomic` 的 `os.replace` 加短暂重试（3 次、间隔递增 0.05s 起）。偶发占用重试即过，OSError 不再发生，combo 正常完成。

```python
def write_json_atomic(path, payload):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path, retries=3, delay=0.05)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

def _replace_with_retry(temp_path, path, retries=3, delay=0.05):
    for attempt in range(retries + 1):
        try:
            os.replace(temp_path, path)
            return
        except OSError:
            if attempt >= retries:
                raise ResultFileWriteError(path) from e   # 重试耗尽 → 专门异常
            time.sleep(delay * (attempt + 1))
```

**第二层（重试耗尽也不误报登录失效）**：重试耗尽时 `write_json_atomic` 抛专门异常 `ResultFileWriteError`（定义在 `scripts/boss/exceptions.py`），子进程顶层捕获并映射为**独立退出码 + 结构化失败行**，上游据此分类为"结果文件写入失败"，与登录失效严格区分。全链路：

1. `scripts/boss/exceptions.py`：新增 `class ResultFileWriteError(RuntimeError)`（结果文件落盘失败，域包异常）。
2. `scripts/boss_cdp_raw.py`（门面，薄映射）：顶层加
   ```python
   except ResultFileWriteError as exc:
       emit_failure_line("source_result_write_failed", str(exc))
       sys.exit(4)   # 新退出码，不与 1/2/3/10/11 冲突
   ```
   用 `emit_failure_line`（`scripts/boss_cdp_signals.py`）输出结构化失败行——这是 webui 唯一权威分类来源（`_classify_failed_code` 的 `parse_failure_line` 直接解析出 `source_result_write_failed`，分类精确，不靠退出码猜）。
3. `webui/error_registry.py`：`_SOURCE_CODES` 加 `source_result_write_failed`（user_message="结果文件写入失败"，retryable=True，非 systemic/blocking——影响单 combo，可重试不阻断全任务）。
4. `webui/src/errorCodes.ts`：镜像同步加 `source_result_write_failed` + 文案（由 `errorCodes.spec.ts` 保证同步）。
5. `webui/source_boss_helpers.py`：`_EXIT_REASONS` 补 `4: "结果文件写入失败"`（作为无失败行时的兜底，虽然用失败行时走 `resolve_code` 不依赖它）。

- `flush_jobs` 合并逻辑（`output.py:75-95`）不动（重试发生在 `write_json_atomic` 内部）。
- **为什么必须加第二层**：仅靠重试解决"偶发占用"，但持续占用（极罕见）时若不分类，仍会退回退出码 1 误报登录失效——违背 spec FR-007/FR-008 与用户"彻底不误报"的需求。第二层保证任何情况下文件写失败都明确报"结果文件写入失败"。

### B080 · 重抓 JD 未进 AI 精筛（后端）

**根因**：`webui/runners/recrawl_task.py:517` `jj = dict(j)` 浅拷贝 target（原 jd 为空），仅设 `jj["job_id"] = jid`，**未把 `fetched_jd` 里的 JD 赋给 `jj["jd"]`** → `match_jds` 收到空 JD → `has_usable_jd` False → `missing_jd_verdict`（"未抓到 JD 无法精筛"）。

**修复**（`recrawl_task.py` line 516-519）：
```python
if jd:
    jj = dict(j)
    jj["job_id"] = jid
    jj["jd"] = jd   # 新增：把 fetched_jd 或 target 的 JD 写入精筛输入
    to_judge.append(jj)
```
- `jd` 变量已在上方 `jd = str(j.get("jd","")).strip() or fetched_jd.get(jid, "")` 取到有效 JD，只需写入 `jj["jd"]`。
- 精筛判定本身（`ai_screening.match_jds` / `screening_jd_gate.has_usable_jd`）不动。

## Complexity Tracking

> 无 Constitution 违反项，无需本表。
