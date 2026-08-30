# Research: 工程还债——全仓质量整修

**Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md) | **输入**: 2026-08-30 全仓深度审查（AST 实测口径）+ grill-me 冻结决策

所有技术决策均基于对当前代码库的实测证据，无 NEEDS CLARIFICATION 项。

## D1. 共享常量家 = 扩充 `webui/constants.py`

- **Decision**: 以既有 `webui/constants.py`（现含 `LOG_TAIL_LINES`）为全后端共享常量与纯函数之家；app.py 内被 14 个模块引用的符号（`_MSG_TASK_NOT_FOUND`、`_OPERATIONAL_ERRORS`、`_FEEDBACK_ERROR_STATUS`、`_MSG_ACCOUNT_NOT_FOUND`、`_MSG_UNSUPPORTED_PLATFORM`、`_MSG_EXPERIMENT_NOT_FOUND`、`_MSG_MANIFEST_NOT_FOUND` 等）迁入，`_public_task_status` 从 task_status.py 既有定义出发统一口径，app.py 保留 re-export。
- **Rationale**: 模块已存在、名实相符；避免再开一个"app_kernel"新门面（宪法原则 I 反对门面增殖）；引用翻转是纯 import 路径改写。
- **Alternatives**: 新建 `webui/app_kernel.py`（拒绝：多一个门面文件）；保持常量在 app.py 仅改 import（拒绝：反向依赖未消除，原则 III 仍被踩）。

## D2. 提示语统一方向 = "任务不存在或已被移除"

- **Decision**: `task_pause_support.py:24` 的文案胜出；`app.py:109` 短文案删除，7 处接口响应随新定义生效。
- **Rationale**: 用户冻结决策——信息更全，符合"出错能查到原因"目标。
- **Alternatives**: 保留两条独立常量（拒绝：用户已拍板统一）；统一为短文案（拒绝：用户选了信息全的那句）。

## D3. 日志方案 = 复用既有 `webui/logging_setup.py`

- **Decision**: 不新建日志设施。`logging_setup.py` 已提供 career-scout.log 的 RotatingFileHandler 与子 logger 工厂（`get_logger`）；批次 4 将约 28 个文件中的吞异常点按三档处理后接入 `get_logger(__name__)`；后端业务模块内散装 `print()`（237 处调用中的业务模块部分）清除，`scripts/` CLI stdout 不受限。
- **Rationale**: 022 已建 career-scout.log + /api/logs 读取通道（log_api.py）；扩用面即可，零新概念。
- **Alternatives**: 引入 structlog/JSON 日志（拒绝：超范围）；新建第二套日志模块（拒绝：重复建设）。

## D4. 吞异常三档分类与白名单机制

- **Decision**: AST 口径（`except Exception/BaseException` 且语句体为单 `Pass`）计数 79 处。分类原则：①业务预期路径 → 改显式返回值/错误码；②有影响失败 → `logger.warning/debug` 留痕（必要时叠加既有 emit_failure_line/task_logs 通道）；③纯清理类（如 store.py 关连接失败）→ 保留 pass 但必须附注释说明，并登记进卫生测试白名单。基线测试在 `tests/test_repo_hygiene.py`：全仓 pass-only 计数 ≤ 白名单外基线，只许下降。
- **Rationale**: 用户冻结"该处理的处理、该记录的记录"；AST 口径经实测可复跑（避免 grep 近似误差——首轮代理的 148 即 grep 高估产物）。
- **Alternatives**: 全部禁止 pass（拒绝：清理类吞噬是正当惯用法，硬禁会催生假日志）；只加 lint 不进卫生测试（拒绝：项目无 lint 工具链，卫生测试是既定执法通道）。

## D5. boss 会话态 = 包内 `scripts/boss/runtime.py`

- **Decision**: 新建 runtime.py 持有会话态（网络会话工厂、活动标志 `_run_active`、共享超时/重试参数）；20 个子模块 132 处 `_facade()`（`sys.modules.get("scripts.boss_cdp_raw")` 回溯）替换为 `from scripts.boss import runtime` 单向引用；boss_cdp_raw.py 保留 `__getattr__` 兼容 re-export + CLI 入口（≤130 行不变更职责）。
- **Rationale**: 与 021 B8 既有依赖注入方向一致；消除"子模块读门面可变全局"的隐藏耦合，boss 包可独立 import 与测试。
- **Alternatives**: 全量依赖注入重构（拒绝：超出行为保持范围）；保留回溯仅加注释（拒绝：原则 III 仍被踩）。

## D6. zhilian 拆分 = 镜像 boss 包域结构

- **Decision**: `scripts/zhilian_cdp_raw.py`（900 行，26 函数，无门面特征的真单体）拆为 `scripts/zhilian/` 四域模块：cdp.py（CDP 原语 ~150）、search.py（登录探测/preflight/fetch_list/风险信号/归一 ~340）、detail.py（批量详情/标签工作器 ~440）、urls.py（host/hash ~40）；原文件退化为 `__getattr__` 兼容壳（≤150）。消费方（source_zhilian_cdp.py、source_zhilian_defaults.py、tests）import 翻转到新模块。
- **Rationale**: boss 家族（021 B8）已验证此结构；对称结构降低认知与维护成本；每域均远低于 800 红线。
- **Alternatives**: 只拆出 detail 大头（拒绝：仍留 500+ 行混合体，红线内但职责不清）。

## D7. task_runners 拆分 = 支撑域 + WorkbenchRunner 独立

- **Decision**: `webui/task_runners.py`（864 行）拆为：`task_runner_support.py`（行 39-277 的支撑助手：stdout 缓冲、卡死/风控分类、载荷/脱敏/校验，~260 行）+ `workbench_runner.py`（WorkbenchRunner 类，~330 行）；task_runners.py 保留 TaskRunner 核心 + re-export（≤400 行）。测试与 app.py 的既有 import 路径全部兼容。
- **Rationale**: 类边界天然清晰（TaskRunner 278-568 / WorkbenchRunner 570-864）；支撑函数无状态可安全搬移。
- **Alternatives**: 三文件全拆（含 TaskRunner 独立）（拒绝：TaskRunner 是既有 patch 面与 import 热点，留原地减小批次 9 波及）。

## D8. recovery 迁出 = `scripts/maintenance/` CLI

- **Decision**: `webui/historical_recovery.py`（990 行）整体迁为 `scripts/maintenance/historical_recovery.py`，加 argparse 三子命令 `preview/prepare/execute`（入参：--db、--result-dir、run id 默认保留两个历史 run）；`webui/task_state_api.py` 删除 3 条 `/api/recovery/*` 路由；测试改为直接调用工具函数/CLI。
- **Rationale**: 能力零损失、脱离生产 API 面；事故已过一个月，界面无入口；`run_id` 形参占位问题随接口删除消失。
- **Alternatives**: 参数化保留接口做成通用修复功能（拒绝：用户冻结"直接撤干净"）。

## D9. 测试打桩迁移 = ctx 显式注入

- **Decision**: `_PATCHABLE_APP_SYMBOLS` 8 符号（boss、_BossCdpSource、ai_service、ScraperExecutor、threading、uuid、os、_theme_path）改为 PipelineContext 构造期显式注入属性；`pipeline_context.__getattr__` 动态门面删除；测试侧 `patch("webui.app.X")` 改为构造注入（fake 对象）或 `patch.object(ctx, "X", ...)`；app.py 的 `_theme_path` 等模块级符号同步出仓。批次独立提交（每符号一提交），放最后（依赖批次 3 完成）。
- **Rationale**: docstring 自述"猴补丁红线"是 021 遗留妥协；注入后生产代码不再为测试变形（原则 III 落地）；改动集中于测试文件，风险隔离。
- **Alternatives**: 保留动态门面仅补注释（拒绝：用户冻结"做进本轮"）。

## D10. 前端类型化 = 依赖契约 `discoveryDeps.ts`

- **Decision**: 新建 `webui/src/composables/discoveryDeps.ts` 定义五域依赖接口（WorkflowDeps/SearchDeps/ExecutionDeps/TasksDeps/ResultsDeps）+ 聚合 `DiscoveryDeps`；`shared: Record<string, unknown>` 袋改为类型化对象，构造后回填 37 处改为显式 wiring（类型错误即时暴露）；5 处 `deps: any = {}` 与 21 处非测试 `: any` 清零；DiscoveryView 瘦身抽出 1 个高内聚区块组件（≤1200 行达标）。
- **Rationale**: strict TS 已开，只差契约补全；物理大拆版用户明确排除；契约先行可增量迁移（从 useDiscoverySearch 打样）。
- **Alternatives**: 引入 Pinia 重构状态（拒绝：超出行为保持+用户排除大拆）；只给 shared 袋加索引签名（拒绝：类型检查仍未真正闭环）。

## D11. Windows CI = windows-latest 作业

- **Decision**: `ci.yml` 新增 windows-latest 作业：与 Linux 作业同构（setup-python 3.11 + uv sync + `uv run python -m unittest discover -s tests` + npm ci + npm test）。GitHub 托管 Windows 运行器自带 Chrome，tests/source、tests/chrome_setup 无需特殊处理。
- **Rationale**: 产品是 Windows 优先桌面应用；托管运行器预装 Chrome，零额外安装步骤。
- **Alternatives**: 只跑后端子集（拒绝：全量在 Windows 全绿正是本批价值）；自建 Windows runner（拒绝：超范围）。

## D12. 发布门禁 = build 前测试 + tag 校验

- **Decision**: `release-macos.yml` 在 DMG 构建前插入测试 job（uv 后端套件），测试不过不产出；`scripts/release_check.ps1` 增加标签校验：读 CHANGELOG 最新 `## [x.y.z]`，`git rev-parse --verify refs/tags/vx.y.z` 必须成功，缺失即 throw（提供 `-SkipTagCheck` 开关供显式豁免并在输出中注明）。
- **Rationale**: 1.8.2/1.7.11 无 tag 无 DMG 的脱节不再重演；EXE 构建侧用户本机执行，检查脚本补位。
- **Alternatives**: 仅文档提醒（拒绝：无强制力）。

## D13. 暴露面收敛 = vars/secrets + 非 root

- **Decision**: `release-macos.yml` 与 `publish_mirror.ps1` 中 14 处服务器地址改为 `${{ vars.MIRROR_HOST }}`、`${{ vars.MIRROR_USER }}`（非 root 部署账号）、`${{ vars.MIRROR_PATH }}`；SSH key 沿用既有 `secrets.MIRROR_SSH_KEY`；known_hosts 固化指纹（指纹值由用户提供，见操作单）。公开文本 IP 清零。
- **Rationale**: 密钥已在 secrets，缺的是 host/user；非 root 支持是服务器加固的代码侧前置。
- **Alternatives**: 改写 git 历史清除已泄露 IP（拒绝：历史不改写为冻结规矩；根治靠服务器侧换 IP，走操作单）。

## D14. 治理规则公开 = 仅宪法入 git

- **Decision**: `.gitignore` 放行 `.specify/memory/constitution.md`（精确路径），其余 `.specify/` 维持忽略；宪法先过卫生测试的凭据/本地路径扫描（其内容本就无敏感项，入跟踪后由卫生测试持续看守）。
- **Rationale**: 用户冻结"只公开宪法，工单留本地"；贡献者可见规矩，模块地图对外即项目说明书。
- **Alternatives**: 全 .specify 公开（拒绝：30 个历史工单是过程文档）；维持私有（拒绝：用户已拍板公开）。
