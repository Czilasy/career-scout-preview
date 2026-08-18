# Implementation Plan: AI 判定链路修复与画像事实收口（B063 + B062 + B058）

**Branch**: `015-ai-verdict-polish` | **Date**: 2026-08-18 | **Spec**: `/specs/015-ai-verdict-polish/spec.md`

**Input**: 已冻结 Spec（三个用户故事：US1 AI 空响应重试、US2 移除默认偏好收口 profile_facts、US3 判定卡片桌面双栏）。

## Summary

三条需求一个批次交付：

1. **B063（US1）**：`call_ai` 把「HTTP 200 + 空流式内容」识别为可重试传输故障，默认重试 2 次；json_decode 型 invalid 不重试；tuning 可覆盖；`ai_raw.log` 增加操作类型字段。
2. **B062（US2）**：删除 `match_jds` 第四层默认偏好；`profile_facts` 契约扩展 `degree_type`（默认统招）/`week_off`/`overtime`，`languages` 从 `core_skills` 拆出；简历分析提示词按「字段填写说明书」逐字段引导；主观字段未体现取最大接受度，客观字段有证据才填；匹配宽松 + caveats，六类硬条件与高危 flag 照常硬约束。
3. **B058（US3）**：判定卡片下方红框区域桌面端左右 50/50，移动端不变，纯 CSS + 组件类名调整。

## Technical Context

**Language/Version**: Python 3.11+（Flask 应用，后端 `webui/` 包）；TypeScript/Vue 3 前端（`webui/src/`）。

**Primary Dependencies**: Flask、requests（流式 AI 调用）、unittest（后端测试）、Vitest（前端测试）。无新增第三方依赖。

**Storage**: `profile_facts` 继续存 `screening_runs.profile_facts_json`（JSON 快照，不新增列）；AI 原始响应存本地轮转日志 `~/.career-scout/logs/ai_raw.log`（`webui/ai_raw_log.py`），不新增表。

**Testing**: 后端 `uv run python -m unittest`（`tests/test_ai.py`、`tests/test_ai_retry.py`、`tests/test_tuning.py` 需迁移契约）；前端 `cd webui && npm test`。

**Target Platform**: 桌面版（Windows EXE / macOS DMG）+ 网页版，同一 Web UI。

**Constraints**:

- `webui/ai.py`（2265 行）与 `webui/app.py`（9482 行）已超宪法单文件上限：本批次新逻辑与新提示词文本**禁止继续追加进这两个文件**，落入新模块。
- B062 不迁移旧数据；旧 `profile_facts` 缺新字段按「未体现」或默认值处理（由 `_validate_profile_facts` 归一化）。
- 移动端 B058 布局不变。
- 后端/前端全量门禁：聚焦测试 → 后端全量 → 前端全量 → `npm run build` → 仓库卫生检查。

**Scale/Scope**: 3 个用户故事，均为既有模块语义调整，无新服务、无新存储、无跨端协议变更。

## Constitution Check

*GATE: 通过。*

- 职责分层：新增 `webui/profile_facts.py`（画像事实契约/枚举/校验/描述）与 `webui/ai_prompts.py`（简历分析 + 精筛提示词组装），`ai.py` 只保留最小接线与 `call_ai` 重试改动。
- 引用方向：`ai.py → profile_facts.py / ai_prompts.py / ai_retry.py / ai_raw_log.py`；`profile_facts.py`、`ai_prompts.py` 只依赖 `error_registry` 与标准库，不反向依赖 `ai.py` / `app.py`。
- 单文件尺寸：`profile_facts.py` ≤ 300 行、`ai_prompts.py` ≤ 400 行（提示词本体另放 `webui/prompt_texts.py` 以防超限）；`ai_retry.py` 增量 ≤ 30 行；`ai.py` 净减（迁出提示词与校验逻辑）。
- 拆分纪律：本批次只搬提示词/校验到新模块，不改 AI 输出契约与 DB 结构；行为变化由失败测试先行定义（US1/US2 各建新测试）。

## File Boundaries

*GATE: 本表即 `/speckit-tasks` 的文件边界来源，用户已冻结构建。*

### Allowed files

| 文件 | 用途 | 预估增量 |
| --- | --- | --- |
| `webui/profile_facts.py`（新） | `profile_facts` 契约枚举（job_type 四值 / degree_type 统招·非统招 / week_off / overtime）、`validate_profile_facts`、`build_profile_facts_description`、主观字段最大接受度归一化 | ≤300 行 |
| `webui/ai_prompts.py`（新） | 简历分析系统提示词（含字段填写说明书）与 `match_jds` 系统提示词（删第四层后版本） | ≤400 行 |
| `webui/prompt_texts.py`（新） | 纯文本常量：字段填写说明书段落、判断规则段落（避免提示词拼进代码逻辑） | ≤250 行 |
| `webui/ai_retry.py` | `DEFAULT_RETRY_POLICY` 增加 `invalid_response`（max_retries=2）空响应策略；`AI_TRANSPORT_RETRY_CODES` 加入 `invalid_response`；文件头注释更新 | +≤30 行 |
| `webui/ai.py` | 仅最小接线：200 空内容分支改为可重试（不 break）；`_validate_profile_facts`/`_build_profile_facts_description`/两段提示词改为从新模块导入；删除第四层段 **净减** | 净减（目标 ≤1800 行） |
| `webui/ai_raw_log.py` | `record_raw_ai_response` 增加 `operation` 参数并写入 payload | +≤15 行 |
| `webui/src/components/JobWorkspace.vue` | 红框区域增加分组容器类（左 `verdict-reason` / 右 `caveats-list` 包进 `.verdict-pair`） | +≤15 行 |
| `webui/src/styles.css` | `.verdict-pair` 桌面 grid 两栏（50/50）、字号缩小；移动端媒体查询保持单列；仅该区域 | +≤35 行 |
| `tests/test_profile_facts.py`（新） | 新字段契约、默认值、宽松校验 | 新文件 |
| `tests/test_ai_prompts.py`（新） | 提示词无第四层、含字段填写说明书、`prompt_texts` 组装正确 | 新文件 |
| `tests/test_ai.py` | 空响应重试契约迁移（`test_empty_response_raw_logged_once` 等）+ FINE_SINGLE 去重确认 | 接触但可控 |
| `tests/test_ai_retry.py` | invalid_response 重试策略契约迁移（L91/L226） | 接触但可控 |
| `tests/test_tuning.py` | `AI_TRANSPORT_RETRY_CODES` 变更后的 manifest 映射断言（L934 附近） | 接触但可控 |
| `webui/src/components/__tests__/JobWorkspace.spec.ts` | 桌面双栏 / 移动端单列断言 | +≤20 行 |

### Forbidden files

- `webui/app.py`、`webui/store.py`、`webui/store_migrations.py`、`webui/pipeline_exec.py`、`webui/tuning.py`、`webui/source.py`、`scripts/*`、`packaging/*`、`.github/*`、`webui/src/styles.css` 之外的样式文件、`webui/src/composables/*`。
- 本批次不改 DB schema、不改 `screening_runs` 列、不新增表。

### New files

- `webui/profile_facts.py`：画像事实契约 + 校验 + 描述（职责：US2 字段模型）。
- `webui/ai_prompts.py`：两处系统提示词组装（职责：提示词策略，不含校验）。
- `webui/prompt_texts.py`：纯文本段落常量（职责：文案仓库，与代码解耦）。
- `tests/test_profile_facts.py`、`tests/test_ai_prompts.py`：聚焦测试。

### Reference direction

- `ai.py → profile_facts.py / ai_prompts.py / prompt_texts.py / ai_retry.py / ai_raw_log.py`
- `ai_prompts.py → prompt_texts.py`；`profile_facts.py → error_registry（无需）`，均不反向依赖 `ai.py` / `app.py`。
- 前端 `JobWorkspace.vue → styles.css`（类名），不新增 composable。

### Line gate

- `ai.py`：≤1800 行（净减）；`profile_facts.py` ≤300；`ai_prompts.py` ≤400；`prompt_texts.py` ≤250；`ai_retry.py` ≤180；`ai_raw_log.py` ≤120；`styles.css` 全局 ≤3330；`JobWorkspace.vue` ≤560。
- 若 `ai_prompts.py` + `prompt_texts.py` 合计超 500 行，全部提示词文本下沉 `prompt_texts.py`，`ai_prompts.py` 只保留组装函数。

### Rationale

- `ai.py` 已 2265 行，B062 的提示词改造与契约扩展若原地加会继续膨胀，违反宪法单文件上限；拆到职责内聚的新模块后 `ai.py` 净减，同时让提示词与校验可独立单测。
- B063 的空响应重试必须先有失败测试定义（`empty_response` 重试成功 / 连续失败耗尽 / json 垃圾不重试），再改 `call_ai`，避免破坏现有 200 直出语义。
- B058 纯展示改动，组件结构不动，只加容器类与全局样式后，聚焦组件测试即可覆盖两条视口路径。

## Verification Gate

*GATE: 全部通过才算交付。*

- 聚焦测试：`tests/test_profile_facts.py`、`tests/test_ai_prompts.py`、`tests/test_ai.py`、`tests/test_ai_retry.py`、`tests/test_tuning.py` + 前端 `JobWorkspace.spec.ts`。
- 后端全量：`uv run python -m unittest discover tests`。
- 前端全量：`cd webui && npm test`。
- 构建：`cd webui && npm run build`（dist 同步）。
- 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene` + `git diff --check` + `git status`。

## Project Structure

### Documentation (this feature)

```text
specs/015-ai-verdict-polish/
├── plan.md             # 本文件
├── research.md         #（本批次合入 plan 的 Technical Context；另行生成）
├── data-model.md       #（Phase 1 输出）
├── quickstart.md       #（Phase 1 输出）
├── contracts/          #（Phase 1 输出）
└── tasks.md            #（/speckit-tasks 输出）
```

### Source Code (repository root)

```text
webui/
├── ai.py                     # 接线 + call_ai 重试改造（净减）
├── ai_retry.py               # invalid_response 空响应策略（微调）
├── ai_raw_log.py             # operation 字段（微调）
├── profile_facts.py          # 【新】画像事实契约/校验/描述
├── ai_prompts.py             # 【新】两处系统提示词组装
├── prompt_texts.py           # 【新】提示词文本常量
└── src/
    ├── components/JobWorkspace.vue   # 红框区域容器类
    ├── styles.css                    # .verdict-pair 两栏/单列
tests/
├── test_profile_facts.py     # 【新】
├── test_ai_prompts.py        # 【新】
├── test_ai.py                # 契约迁移 + 新用例
├── test_ai_retry.py          # 契约迁移
└── test_tuning.py            # 契约迁移
```

**Structure Decision**: 沿用现有 `webui/` 单包结构，不新增目录层级；提示词与画像事实按领域拆为平级模块，符合宪法「新功能默认落入对应域的新模块」。

## Complexity Tracking

无宪法违规项需豁免（`ai.py` 净减而非净增；参考方向单向向下）。