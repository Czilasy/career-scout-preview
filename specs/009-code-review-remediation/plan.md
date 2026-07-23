# Implementation Plan: 009 代码审查整改

**Branch**: `feat/009-code-review-remediation` | **Date**: 2026-07-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from [specs/009-code-review-remediation/spec.md](spec.md)

---

## Summary

把 `CODE_REVIEW.md` 中识别的 28 类问题（A 代码优化 + B 逻辑优化）按四波次序落地。本 plan 细化第 1+2 波的可执行设计（零风险清理 + 性能/竞态修复），第 3+4 波作为「后续波次」占位，等第 2 波合并后基于真实基线补 tasks。store.py 拆分按 spec 推迟触发条件挂起。

技术路径：
- 第 1 波以机械删除 / 重命名 / 文件新增为主，零行为变更，跑测试即可验证
- 第 2 波以 SQLite 事务（`BEGIN IMMEDIATE`）/ UPSERT / 索引 / 批量查询为主，每处配真实并发集成测试
- 第 3+4 波占位记录设计原则与依赖关系，不细化到任务级

---

## Technical Context

**Language/Version**: Python 3.10+（webui 后端）/ TypeScript 5.x + Vue 3 `<script setup>`（前端）/ Node 18+（构建）

**Primary Dependencies**:
- 后端：Flask、requests、websocket-client、keyring、pypdf、python-docx、sqlite3（标准库）
- 前端：Vue 3、Vite、TypeScript strict、（待引入）pinia 或简单 reactive 模块
- 构建/工具：uv（Python 包管理）、npm（前端构建）、GitHub Actions（CI）

**Storage**: SQLite（单文件 `data.db`，通过 `TaskStore` 类管理；新增 migration 不破坏现有 schema）

**Testing**:
- 现有：`python -m unittest discover tests`（25 个测试文件，全 mock）
- 新增：`tests/test_concurrency.py`（真实 SQLite 并发集成测试，验证 A1/A3）
- 现有：`npm run build`（前端 TypeScript strict + Vite 构建）
- 新增：`tests/test_indexes.py`（验证索引被 EXPLAIN QUERY PLAN 命中）

**Target Platform**: Windows 本地开发（用户主力）+ Linux CI runner（GitHub Actions ubuntu-latest）

**Project Type**: web-service（Flask 后端 + Vue SPA 前端）+ cli-tool（boss_cdp_raw.py 抓取脚本）

**Performance Goals**:
- `list_analyses` 在 1000 条数据下响应时间下降 ≥ 50%（A5 批量化前后对比）
- `cleanup_expired_jobs` 在 100 万行 jobs 表上 EXPLAIN QUERY PLAN 命中 `idx_jobs_expires_at`（A6）
- 前端 `npm run build` 0 error 0 warning（TypeScript strict，A10/A11 删索引签名与死代码后）

**Constraints**:
- AGENTS.md 单文件原则：`scripts/boss_cdp_raw.py` 不拆分，仅做异常收窄
- 第 4 波机械迁移硬约束：路由 URL 不变、行为不变、不做接口重设计
- SQLite 版本 ≥ 3.24（支持 `ON CONFLICT ... DO UPDATE`）；若需 `RETURNING` 需 ≥ 3.35，FR-2.2 退路为 `BEGIN IMMEDIATE` 包裹的 SELECT-then-UPDATE

**Scale/Scope**:
- 后端：app.py 2660 行、store.py 3708 行（单体，本 spec 推迟 store.py 拆分）
- 前端：DiscoveryView.vue 741 行、styles.css 1310 行
- 测试：25 个测试文件，新增 2 个（并发集成 + 索引验证）

---

## Constitution Check

**GATE**: Must pass before Phase 0 research. Re-check after Phase 1 design.

*Gates determined based on constitution file*

**No constitution file**（`.specify/memory/constitution.md` 不存在）。回退到项目硬规则（AGENTS.md + 用户规则）：

| Gate | 来源 | 状态 | 说明 |
|---|---|---|---|
| 核心逻辑单文件原则 | AGENTS.md | PASS | 本 spec 不动 `scripts/boss_cdp_raw.py` 内部抓取逻辑，仅做 except 收窄（A4 子进程代码侧） |
| 版本号四处一致 | AGENTS.md | N/A | 本 spec 不改版本号 |
| 异常处理禁止 bare except | AGENTS.md | PASS | FR-3.1 收窄 except Exception 为具体类型，方向一致 |
| README 双语同步 | AGENTS.md | N/A | 本 spec 不改用户可见行为（除错误响应结构），无需改 README |
| Conventional Commits | AGENTS.md | PASS | 每波拆多个 commit，用 `fix:` / `refactor:` / `perf:` / `ci:` 前缀 |
| webui 改完重启五步纪律 | AGENTS.md + 用户规则 | PASS | 第 3 波改完后端/前端代码后必须走完整五步重启 |
| 一次性把文档所有内容 spec 化 | 用户确认 | PASS | spec.md 已覆盖 28 类问题 + 跨波次约束 + 后续待办 7 条 |
| plan/tasks 细化到第 1+2 波 | 用户确认 | PASS | 本 plan 细化第 1+2 波，第 3+4 波占位 |

**Gate 结论**：无违反，可进入 Phase 0。

---

## Project Structure

### Documentation (this feature)

```text
specs/009-code-review-remediation/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出（技术决策与备选方案）
├── data-model.md        # Phase 1 输出（新增索引与表结构变更）
├── quickstart.md        # Phase 1 输出（端到端验证指南）
├── contracts/
│   └── http-api.md      # Phase 1 输出（错误响应统一契约）
└── tasks.md             # Phase 2 输出（/speckit-tasks，仅第 1+2 波）
```

### Source Code (repository root)

```text
# 第 1 波涉及
.github/workflows/ci.yml              # 新增（FR-1.6）
webui/constants.py                    # 新增（FR-X.7 魔法数字集中）
webui/app.py                          # 删重复 import、_pipeline_tasks 清理（FR-1.3/1.5）
webui/store.py                        # 删重复 import、_copy_legacy_default_profile 短路、link_direction_evidence 合并（FR-1.3/X.8）
webui/src/components/BaseDialog.vue   # previousFocus 改 ref（FR-1.4）
webui/src/types.ts                    # 删 JobItem 索引签名、删 SelectOption（FR-1.2/X.1）
webui/src/App.vue                     # 删 currentProfile computed、内联副作用抽方法（FR-1.2/X.8）
webui/src/views/DiscoveryView.vue     # 删 groupsForResult 薄包装（FR-1.2）
webui/src/components/JobWorkspace.vue  # 删 selectedId/select/watch 死代码、host 校验简化（FR-1.2/X.2/X.8）
webui/src/api.ts                      # .catch 改 console.warn（FR-X.8）
webui/src/styles.css                  # 删 15 个死样式类（FR-1.2）
scripts/boss_cdp_raw.py               # 删 append_json（FR-1.2）
uv.lock                               # uv sync 重新生成（FR-1.1）

# 第 2 波涉及
webui/store.py                         # append_log/create_analysis/create_confirmation 事务包裹（FR-2.1）
                                       # save_job UPSERT（FR-2.2）
                                       # list_analyses 批量化（FR-2.3）
                                       # 新增 migration 创建索引（FR-2.4）
                                       # cleanup_expired_jobs 改单 SQL（FR-X.5）
webui/app.py                          # search_run_jobs/latest_pipeline_result 批量化（FR-2.3）
                                       # ai_settings_models 返回 502（FR-2.5）
webui/src/views/DiscoveryView.vue     # pollTask 退避 + retryCount（FR-2.6）
tests/test_concurrency.py             # 新增（A1/A3 并发集成测试）
tests/test_indexes.py                 # 新增（A6 索引验证）

# 第 3 波（占位，不在本 plan 细化）
# 第 4 波（占位，不在本 plan 细化）
```

**Structure Decision**: 沿用现有目录结构。本 spec 是整改而非新增功能，不引入新顶层目录。第 1 波新增 `.github/workflows/` 与 `webui/constants.py` 两个文件；第 2 波新增 2 个测试文件；其余均为现有文件就地修改。

---

## Complexity Tracking

> 无 Constitution Check 违反，本节为空。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

---

## 后续波次（占位，不细化到任务级）

### 第 3 波 · 异常处理 + 前端状态机

**设计原则**：
- except Exception 收窄按 spec FR-3.1 区分库代码（收窄为具体类型）与子进程代码（保留宽捕获 + logger.exception）
- 错误响应统一到 discovery envelope，legacy 路由用 Flask errorhandler 包装，不动 URL
- 收藏状态提升到 `webui/src/stores/favorites.ts`（简单 reactive 模块，避免引入 pinia 增量依赖）
- setPipelineResult 恢复全部运行时标识符（scrapeTaskId / screenTaskId / rejectedIds），非只恢复 result
- AiSettingsDialog 用 AbortController 取消网络请求

**依赖**：第 2 波合并后启动；硬编码颜色替换依赖第 1 波 styles.css 死样式删除完成（避免改了又改）

**风险**：异常收窄可能暴露原本被吞的真实 bug，需先观察日志一周再判断是否修复根因

### 第 4 波 · 架构拆分（不含 store.py）

**设计原则**：
- app.py Blueprint 拆分以「机械迁移、URL 不变、行为不变」为硬约束，不做接口重设计
- DiscoveryView 拆分前先用 vue-devtools 记录现有状态流转，拆分后逐 step 跑端到端冒烟测试
- ChromeSessionManager 引用计数 + 超时兜底（≥ 10 分钟无活动强制关闭）+ 日志记录每次 acquire/release
- 两套任务系统统一到 DB 那套（tasks 表 + kind 字段），删除 _pipeline_tasks 内存 dict

**依赖**：第 3 波合并后启动；需要先画依赖图识别全局状态（_pipeline_tasks / app.config）的迁移路径

**风险**：
- Blueprint 拆分漏移全局状态 → 全局状态统一迁到 `app.extensions` 或 `current_app` 上下文
- DiscoveryView 拆分破坏 emit/props 链 → 拆分后逐 step 冒烟测试
- ChromeSessionManager 引用计数错误 → 超时兜底 + 日志可观测

**store.py 拆分推迟**：按 spec 推迟触发条件挂起，本 plan 不涉及

---

## Done When

- [ ] 第 1 波全部 FR-1.x 与编入第 1 波的 FR-X.x 完成，测试全绿
- [ ] 第 2 波全部 FR-2.x 与编入第 2 波的 FR-X.x 完成，并发集成测试通过
- [ ] research.md 完成（Phase 0）
- [ ] data-model.md 完成（Phase 1）
- [ ] contracts/http-api.md 完成（Phase 1）
- [ ] quickstart.md 完成（Phase 1）
- [ ] tasks.md 完成第 1+2 波任务（Phase 2，由 /speckit-tasks 生成）
- [ ] 第 3+4 波在 plan 中作为占位记录，tasks.md 留 `# 后续波次（待激活）` 段
