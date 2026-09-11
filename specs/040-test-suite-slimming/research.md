# Phase 0 Research: 测试体系低噪音瘦身

本文件记录关键操作决策、理由与被否决的替代方案。所有决策对齐已冻结 spec.md。

## 决策 1：故障注入指纹法的操作定义（FR-002）

- **Decision**：在主工作区执行"改-跑-还原"三联操作：① `git status` 确认目标产品文件干净 → ② 对目标产品逻辑做最小注入（如让某判断恒真/恒假）→ ③ 跑"受影响测试集"（按该产品符号的引用方测试文件确定范围）并记录"变红集合" → ④ `git checkout -- <产品文件>` 还原 → ⑤ `git status` 复核干净。选边改动**前**跑一次（指纹 A），改动**后**再跑一次（指纹 B），A = B 才算通过。
- **Rationale**：产品文件均在 git 跟踪内、工作区初始干净，还原可机械验证；无需复制整个仓库（65k 行测试的副本代价高）。
- **Alternatives considered**：① 临时完整副本——重且慢；② `git stash`——会把未提交的测试改动一起藏起，干扰；③ `git worktree`——可行但同样重，且本任务改动频繁、不划算。
- **例外约定**：若注入点所在产品文件恰有未提交改动，先完成/备份该处再注入；注入期间不得进行其它文件操作，还原后必须复核。

## 决策 2：构建指纹排除测试文件（批四）

- **Decision**：`webui/vite.config.ts` 的 `frontendCandidates` 在收集后过滤测试相关路径（`__tests__/` 目录、`*.spec.ts`、`src/test/` 目录）；`backendFiles` 不变（本就只含生产文件）。
- **Rationale**：测试文件不参与产物构建；当前它们在候选集内，导致"只改测试也触发 pre-push 重建"（`hooks/pre-push` → `webui/ensure_frontend_sync.py`）。
- **风险与缓解**：候选集变化会使既有 `dist` 与新指纹不一致 → 下一次前端同步检查时重建一次（一次性成本，可接受）；运行时校验常量 `__EXPECTED_BACKEND_BUILD_HASH__` 取自 backend digest，不受影响。
- **Alternatives considered**：① 不改——每次改测试都白重建；② 修改 `ensure_frontend_sync.py`（产品代码，越界）。

## 决策 3：提交前脚本去本机硬编码路径（批四）

- **Decision**：移除 `hooks/pre-commit` 中 `"D:/ana/python.exe"` 这一固定候选，保留其余通用候选路径与"uv 失败→逐候选回退"的既有逻辑。
- **Rationale**：公开仓库文件不得内嵌本机绝对路径（AGENTS 文件边界）；仅删除一行候选，不影响兜底能力与失败行为。
- **Alternatives considered**：改为读取环境变量配置——增加环境依赖与复杂度，收益低。

## 决策 4：假保护修复与"真实执行"验证（批一）

- **Decision**：对每条假保护，**先写下"注入什么会红"**，再改为真执行路径（真服务/真 store/真断言/当前选择器），改后注入并确认变红，然后移除注入并留档验证记录。
- **Rationale**：spec FR-003 要求修复后必须能被故障注入触发；同一方法可同时证明"修复有效"与"保护真实"。
- **Alternatives considered**：直接删除（仅当用例确零价值时允许，且在批末报告中说明理由）。对 `test_pipeline_guard.py` L245-261（白箱事件上下文契约）优先"真 `WhiteboxService` + 真 store（临时库）"修复而非删除。

## 决策 5：永久跳过用例的处置（批一）

- **Decision**：`tests/healthy_pipeline/test_pipeline_state.py` 中因 `specs/010-healthy-pipeline-recovery` 目录不存在而条件跳过（L781-800）的两条用例**删除**。
- **Rationale**：该目录在公开仓库永不存在，条件恒假 = 永远不执行；规格要求"不再存在'任何情况下都不执行'的用例"。
- **Alternatives considered**：改为显式环境变量开关——默认仍不执行，无实际增益，徒增分支。

## 决策 6：Windows 平台跳过用例的覆盖缺口（批一）

- **Decision**：`tests/test_update_manifest.py` L55-57（权限位）与 `tests/test_logging_setup.py` L102-116（日志文件删除重建）在 Windows 本机恒跳过：改为**不依赖 POSIX 权限/句柄语义的等价变体**（如用只读属性、占用句柄模拟），使 Windows 本机也执行；若确无等价手段，保留跳过并在用例 docstring 注明平台原因与覆盖缺口，同时上报。
- **Rationale**：审计建议"补而不是删"；本机覆盖缺口应尽量收口。

## 决策 7：全局状态还原规范（批一/批四）

- **Decision**：凡修改全局状态（打补丁、环境变量、模块级替换、随机种子）的用例，统一用 `unittest.mock.patch.object` 上下文管理器或 `addCleanup` 注册还原；禁止"手动替换、tearDown 只恢复部分符号"的模式（`test_account_round_robin.py` 的 `mark/clear_account_rate_limited` 泄漏即此模式）。
- **Rationale**：spec FR-006；防止跨文件污染与顺序依赖。

## 决策 8：统计口径与基线（FR-015）

- **Decision**：统计固定为——后端文件数/行数（`tests/` 下 `*.py`）、前端文件数/行数（`*.spec.ts`）、用例数（全量输出 `Ran N tests` / `Test Files N, Tests N`）、全量时长（实测）。基线：后端 127 / 65,697 行 / 3163 例（约 19 分钟）；前端 52 / 21,402 行 / 872 例（约 26 秒）。
- **Rationale**：可复现、可对比、与 SC-005/SC-006 一致。

## 决策 9：同一批全链路整跑 3 遍的消除（批三）

- **Decision**：`tests/webui_app/test_webui_app_semantics.py` 中两个子类以**继承**方式复用 `test_cross_platform_dedupe.py` 的 11 个重型集成用例（同一批真实线程/临时库跑 3 遍）。改为**组合**：把复用的用例体抽为可调用的普通函数或 mixin 方法，两个子类按需调用，保留各自独有断言。实施前对跨平台去重核心逻辑做一次故障注入指纹。
- **Rationale**：审计确认的全仓最大单笔重复；组合方式不减少任何断言。
- **Alternatives considered**：① 保留继承（问题依旧）；② 删除子类（丢失各自的筛选/恢复语义断言）。

## 决策 10：真实外部资源触碰的处置分级（批一/批四）

- **Decision**：逐项定性处置——① 产品默认行为被真实触发（如 `ensure_chrome_ready` 真探测端口、桌面壳真写用户目录）：**打桩/传临时目录修掉**（批一）；② 真实依赖是测试语义本身（如 `process_executor` 真 `taskkill`、`repo_hygiene` 读本地 git、`chrome_setup` 真子进程）：**保留但确保边界可控**（不用真实用户目录、不残留进程），必要时在用例 docstring 注明"真实资源依赖"；③ 手动工具（`sc015_viewport_check.py`、`run_isolated_webui.py`、`sc002_24h_monitor.py`）：**与自动测试分离**，脚本头部注明"手动资产，不进 CI"，`sc015` 的选择器在本批修正。
- **Rationale**：spec FR-005；区分"真缺陷"与"有意的真实集成"，避免过度打桩丢失集成价值。
