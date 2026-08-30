# Research: 续跑账号身份修复

**Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## R1: "用户主动换号"的判定信号

**Decision**: 任务创建时在 `execution_params` 记录快照键 `active_account_at_freeze`（当时全局当前账号）；统一继续接口判定"当前全局账号 ≠ 快照"为用户主动换号。

**Rationale**: 现行判定"当前全局账号 ≠ 冻结账号"无法区分"用户暂停期间换了号"与"R2 角色账号从创建起就不同于全局"。快照把两种状态解耦：创建时全局=d、冻结=b（R2）→ 未动账号时当前仍=d=快照 → 不换；B057 场景创建时全局=a=快照，暂停期间激活 b → 当前 b ≠ 快照 a → 换。跨服务重启有效（快照持久化在 execution_params，全局账号持久化在高级设置）。

**Alternatives considered**:
- 仅按暂停码门槛（浏览器/账号类才考虑换号）：无法区分"用户为下个任务改全局"的误伤；且快照已覆盖其主要缺陷。
- 记录暂停时刻快照：暂停写入点分散（hard_stop/卡死/AI 暂停/用户暂停等多处），遗漏风险高；创建点只有三处，可靠。
- 前端显式传 target：改前端契约，超出冻结范围。

## R2: AI 类暂停码集合

**Decision**: 在 `resume_identity.py` 定义显式冻结集合 `AI_PAUSE_CODES = {"ai_rate_limited", "ai_quota_exhausted", "ai_key_invalid", "ai_network_error"}`，与 `webui/app_support.py` `_check_resume_block` 既有集合同口径，注释标注来源为 `error_registry.py` 的 "ai" 类目。

**Rationale**: 代码库既有惯例就是显式集合（app_support 同款写法）；动态从 error_registry 按类目派生看似更稳，但 registry 条目结构为内部实现、派生逻辑反而引入耦合，且 AI 码集合历史上极稳定。

**Alternatives considered**: 按类目动态派生——被否，耦合 registry 内部结构；直接 import app_support 的局部集合——被否，该集合是函数局部变量且 app_support 为工厂文件。

## R3: 存量任务缺快照的语义

**Decision**: 快照键缺失 → 不自动换号（沿用冻结身份）；显式 target_account 不受影响。

**Rationale**: 保守方向与 R2 契约一致（本缺陷的受害者恰是无快照的存量 R2 任务）；B057 面板切号流对存量任务退化为"显式选择"，可接受且可预期。

**Alternatives considered**: 缺失视为旧行为（自动换）——被否，等于对本缺陷的目标人群保持 bug。

## R4: BOSS 缺冻结账号的兜底统一口径

**Decision**: 统一继续接口与 `continue_recrawl` 的兜底改为：BOSS → `account_for_role("R2", run=run, fallback=当前全局账号)`；智联 → 保持 `account_for_run(run)`。解析结果写回冻结身份（沿用 `persist_frozen_identity`）。

**Rationale**: 与 `ai_screen_api.py` 续跑路径（`account_for_role("R2", run=prev)`）同口径；`account_for_role` 的冻结值优先语义天然兼容（有冻结值时直接返回，行为不变；无冻结值时角色解析，与筛选提交入口一致）。

**Alternatives considered**: 在 resolve_frozen_identity 内改父借逻辑——被否，父借对 cdp_port/profile_key 仍必要，只应调整账号字段的兜底优先级。

## R5: 换号可见化的实现位置

**Decision**: 双门槛命中后：① `store.append_task_event(run_id, "account_switch", {...原/新账号标识})`；② 续跑启动时向内存任务 `logs` 追加一行中文提示（前端进度界面现有渲染直接显示）。不改前端代码、不加组件。

**Rationale**: 任务事件已进诊断接口（审计）；logs 是用户实际观看的进度流，零前端改动即可见。账号名称经账号簿 `load_browser_accounts` 取 name，缺账号时回退显示账号 id。

**Alternatives considered**: 前端 toast——被否，需改快照契约与前端，超出冻结范围；仅事件不写 logs——被否，用户主界面不可见，可见化目的打折。

## R6: job-detail 409 门禁的判定口径

**Decision**: 复用 `ctx.has_active_pipeline_task()`（与 AI 筛提交入口同口径），命中即返回 409 + 中文提示（"当前已有任务在运行，请稍后再试"风格）。置于身份继承/浏览器激活之前，先查先拒。

**Rationale**: 全站任务入口已有统一并发语义，直接对齐；放在浏览器激活前避免"先改全局再拒绝"的副作用。

**Alternatives considered**: 仅阻止浏览器操作放行抓取——被否（用户质询选定 409）；加队列——被否，过度设计。

## R7: JD 阶段重绑的调用点

**Decision**: `runners/ai_screen_jd.py` `run_jd_stage` 在 `ensure_chrome_ready` 调用之前执行 `ctx.activate_task_browser(task_id)`（与 `recrawl_task.py` 既有模式一致，按冻结账号重绑）。

**Rationale**: 把重绑窗口从"runner 启动"收窄到"JD 阶段前一刻"，粗筛阶段的分钟级暴露窗口消除；复用既有注入方法，无新依赖。

**Alternatives considered**: ensure_chrome_ready 增加冻结 profile 参数——被否，改动公共签名波及所有调用方。

## R8: 快照写入的三个创建点

**Decision**: `exec_search_api.py`（抓取创建）、`ai_screen_api.py`（AI 筛创建）、`pipeline_jobs_api.py`（重抓创建，含单岗位重抓）在 execution_params 构造处经 `resume_identity` 提供的助手写入快照（取当时全局账号）。

**Rationale**: 与 FR-001 一一对应；助手统一取值口径（高级设置 `browser_account`，与 `_account_for_run(None)`/continue 判定同源），避免三处各写各的。

**Alternatives considered**: 在 store.create_screening_run 内统一注入——被否，store 层不应感知账号语义，且 create_screening_run 是 INSERT OR REPLACE 通用入口。
