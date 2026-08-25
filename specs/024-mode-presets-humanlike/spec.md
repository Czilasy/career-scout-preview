# Feature Specification: 三档模式数值重设计 + 人形模拟行为 + 风险警示区

**Feature Branch**: `024-mode-presets-humanlike`

**Created**: 2026-08-25 | **Status**: Draft（grill-me 冻结需求，14 条）

**Input**: 用户冻结需求（grill-me 确认，14 条）：

1. 三档 11 个已有字段按冻结表数值落地，一档一套、三规模同值（3×3 结构保留）；2. 极限档 = 固化当前自定义值（10/10/30/2/4/5/5/50/5/5/5），与 custom 解耦，用户改 custom 不影响极限档；3. 新增模拟行为 3 项（详情加载随机等待、人形滚动、鼠标移动概率），作为内部配置随档位下发，不进高级设置 UI；4. 详情抓取补全行为仿真（`detail_scrape.py`）：加载后随机等待、正常路径人形滚动、随机鼠标移动；5. 滑块配色：稳定=绿、平衡=黄、极限=红、自定义=默认色；6. 极限档黄色警告（"极高概率限流封号"）显示于模式选择器正下方警示区；7. 任务规模新口径：总页数 <15 小 / 15~30 中 / >30 大（替换旧 9/49 阈值）；8. 大任务（>30 页）任何档位下显示"任务规模过大可能封号"警告；9. 极限警告 + 大任务警告合并显示于同一黄色警示区，可同时出现；都不满足则隐藏；固定显示不可关闭；10. pages 输入范围收紧 1~200（对齐后端上限）；11. 发布后保持用户当前档位选择（custom）不变；12. README 同步档位说明；13. 版本 minor 提升；14. 验证门禁：聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 仓库卫生检查。

**冻结档位数值表**（11 字段；#2-#11 为速度配置快照字段，#1 pages 不属于快照，见 Assumptions）：

| # | 字段 | 稳定 | 平衡 | 极限 |
|---|---|---|---|---|
| 1 | pages | 2 | 5 | 10 |
| 2 | inter_combo_delay | 20 | 13 | 10 |
| 3 | detail_batch_size | 15 | 20 | 30 |
| 4 | detail_interval | 15 | 10 | 2 |
| 5 | detail_reset_every | 2 | 3 | 4 |
| 6 | detail_batch_cooldown | 15 | 10 | 5 |
| 7 | detail_tab_pool_size | 2 | 3 | 5 |
| 8 | screen_batch_size | 30 | 40 | 50 |
| 9 | screen_concurrency | 3 | 4 | 5 |
| 10 | match_batch_size | 3 | 4 | 5 |
| 11 | match_concurrency | 3 | 4 | 5 |

**模拟行为表**（内部字段，不进 UI，随档位走）：

| # | 行为 | 稳定 | 平衡 | 极限 |
|---|---|---|---|---|
| 12 | 详情加载等待（随机区间，秒） | 5-10 | 3-6 | 1-2 |
| 13 | 详情滚动（次数） | 3-7 | 2-4 | 0-1 |
| 14 | 鼠标移动概率 | 50% | 30% | 无 |

## User Scenarios & Testing

### User Story 1 - 三档数值一档一套、极限档固化当前自定义值 (P1)

用户在高级执行设置里选择「稳定 / 平衡 / 极限」档位时，得到的不再是旧的三规模递增数值，而是本表冻结值：每档一套、三个任务规模（小/中/大）同值；极限档固定为当前自定义值（10/10/30/2/4/5/5/50/5/5/5）。用户之后改自定义档位的值，不影响极限档。

**为什么是 P1**：这是本次需求的主体，全部档位行为的地基。

**独立测试**：后端测试断言 `get_mode_config(mode, task_size)` 三档新数值、三规模同值、extreme 等于固化值；`select_mode` 返回的 config 与 custom 解耦（custom 保存新值后 extreme 不变）。

**验收场景**：

1. **Given** 无活动模式版本（默认路径），**When** 取 stable/balanced/extreme 任意规模的配置，**Then** 数值与冻结表一致，且三规模返回值相同。
2. **Given** 当前档位为 extreme，**When** 用户把 custom 配置改为任意值并保存，**Then** extreme 档数值保持不变（固化）。
3. **Given** 选择任意档位，**When** 读取配置快照，**Then** 返回内容不含 `pages` 字段（FR-009 维持）。
4. **Given** 数据库存在活动模式版本（调优产物），**When** 选择档位，**Then** 仍走 `matrix[mode][task_size]`，不自动改写该版本数据。

### User Story 2 - 详情抓取人形模拟行为（内部，无 UI）(P1)

详情抓取（JD）时，浏览器行为更像真人：详情页加载完成后随机等待（稳定 5-10s / 平衡 3-6s / 极限 1-2s），随后按档位做人形滚动（稳定 3-7 次 / 平衡 2-4 次 / 极限 0-1 次，随机距离、偶尔回滚），并以档位概率随机移动一次鼠标（稳定 50% / 平衡 30% / 极限不做）。这些行为由当前执行档位决定，不出现在高级设置 UI 中。

**为什么是 P1**：需求明确点名 `detail_scrape.py` 补全"像人"路线，是限流规避的关键一环。

**独立测试**：测试注入 fake CDP 会话与 sleeper，断言加载后等待时长落在档位区间、滚动调用次数落在档位区间、鼠标移动按概率触发（概率用随机种子或注入随机源）；CLI/in-process 翻译链路断言 `--simulation-mode` 参数贯通。

**验收场景**：

1. **Given** 档位为 stable，**When** 抓取一个详情，**Then** 加载完成后随机等待 5-10s、人形滚动 3-7 次、以 50% 概率移动鼠标。
2. **Given** 档位为 extreme，**When** 抓取一个详情，**Then** 等待 1-2s、滚动 0-1 次、不移动鼠标。
3. **Given** 未传模拟参数（`simulation_mode=None`，CLI 直跑等旧路径），**When** 抓取详情，**Then** 行为与现状完全一致（零仿真），不破坏既有契约。
4. **Given** 高级设置 UI 打开，**When** 查看设置项，**Then** 不出现任何模拟行为相关字段。

### User Story 3 - 滑块配色 + 黄色警示区（极限警告 / 大任务警告）(P1)

高级执行设置中，档位相关控件按档位着色：稳定=绿、平衡=黄、极限=红、自定义=默认色。模式选择器正下方固定显示一个黄色警示区（一行字横幅，不可关闭、无叉）：极限档时显示"极高概率限流封号"；任务规模为大（>30 页）时显示"任务规模过大可能封号"；两条可同时显示；都不满足则整体隐藏。

**为什么是 P1**：风控提示是用户确认的必须项，且涉及前端新组件与规模口径联动。

**独立测试**：组件测试渲染警示区（只传极限档、只传大任务、两者同时、两者都没有四种状态）；Discovery 状态层测试断言规模口径变化触发大任务警告。

**验收场景**：

1. **Given** 选择极限档且任务规模非大，**When** 渲染设置区，**Then** 警示区显示"极高概率限流封号"一行黄字。
2. **Given** 任何档位（含稳定、自定义）且总页数 >30，**When** 渲染设置区，**Then** 警示区显示"任务规模过大可能封号"。
3. **Given** 极限档 + 大任务同时满足，**When** 渲染设置区，**Then** 两条警告同时显示在同一黄色区域。
4. **Given** 非极限档且任务规模 ≤30，**When** 渲染设置区，**Then** 警示区隐藏。
5. **Given** 警示区可见，**When** 查找关闭按钮/叉号，**Then** 不存在，固定显示不可关闭。

### User Story 4 - 任务规模新口径与 pages 范围收紧 (P1)

任务规模判定改口径：总页数（组数 × 每组合页数）<15 为小、15~30 为中、>30 为大，替换旧 9/49 阈值；pages 输入范围收紧为 1~200（对齐后端上限，旧前端范围 1~9999）。

**为什么是 P1**：需求 5/10 明确，且规模口径直接影响警示区与档位选择。

**独立测试**：后端 `classify_task_size` 边界测试（14→small、15→medium、30→medium、31→large、200→large、201 拒绝）；前端 `classifyTaskSize` 同步更新；pages 输入 min/max 断言。

**验收场景**：

1. **Given** 总页数 14，**When** 分类，**Then** 小任务。
2. **Given** 总页数 15 与 30，**When** 分类，**Then** 中任务。
3. **Given** 总页数 31，**When** 分类，**Then** 大任务。
4. **Given** 高级设置 pages 输入框，**When** 查看 min/max，**Then** 1~200，输入 201+ 被钳制/拒绝。

### User Story 5 - 发布后保持用户当前档位选择 (P2)

版本发布后，用户打开应用仍停留在其当前档位（现在是 custom），不因三档重设计被重置为默认档。

**为什么是 P2**：依赖 US1 的配置迁移，属于数据兼容要求。

**独立测试**：`advanced_config_state.active_selection` 保持不变（无迁移逻辑重置选择）；前端加载后 selection 与发布前一致。

**验收场景**：

1. **Given** 发布前 active_selection=custom，**When** 升级到新版本，**Then** 仍为 custom，且 custom 配置不被改写。

### User Story 6 - 文档与版本收口 (P3)

README 同步档位说明（三档数值表、模拟行为说明、pages 范围、警示语义）；版本 minor 提升（`scripts/bump_version.py` 同步各清单文件 + CHANGELOG）。

**为什么是 P3**：工程收口项，依赖前面全部功能落地。

**独立测试**：README 章节存在且数值与冻结表一致；`pyproject.toml`/`webui/package.json`/`uv.lock` 等版本号同步；CHANGELOG 新增 minor 条目。

**验收场景**：

1. **Given** 功能全部落地，**When** 提升 minor 版本，**Then** 全部版本清单文件一致、CHANGELOG 更新。
2. **Given** 提交/推送前，**When** 运行卫生检查，**Then** 通过。

### Edge Cases

- 大任务 + 极限档同时满足：两条警告同时显示（同一黄色区域，分行）。
- custom 档位：不显示极限警告；大任务警告仍显示（"任何档位"含 custom）。
- 任务规模边界：总页数恰为 15 / 30 / 31 的分类正确。
- pages 输入 0、负数、非数字、>200：被 UI 钳制/校验拒绝；后端 1~200 校验不变。
- 数据库存在活动模式版本（调优实验产物）时，三档新数值不覆盖该 matrix；新数值仅作用于无版本默认路径（见 Assumptions A2）。
- 串行与并行两条详情抓取路径都要接入模拟行为（`_scrape_one_detail` 与 `_scrape_detail_on_tab`）。
- 未传 simulation_mode 的旧调用（CLI 直跑、测试替身）零仿真，行为与现状一致。
- 模拟行为不影响 safe event 契约（duration_ms 仍为真实耗时；不新增事件字段）。
- 警示区在移动端窄屏下不换行挤压布局（一行或折行均可接受，但必须完整可见）。

## Requirements

### Functional Requirements

- **FR-001**: 三档 10 个速度字段 MUST 按冻结表落地，一档一套、三规模同值（3×3 matrix 结构保留，三规模槽位填同一组值）。
- **FR-002**: 极限档 MUST 固化当前自定义值（inter_combo_delay=10, detail_batch_size=30, detail_interval=2, detail_reset_every=4, detail_batch_cooldown=5, detail_tab_pool_size=5, screen_batch_size=50, screen_concurrency=5, match_batch_size=5, match_concurrency=5）；用户修改 custom 配置 MUST NOT 影响极限档。
- **FR-003**: 模式配置快照 MUST NOT 包含 `pages`（FR-009 维持）；`get_mode_config`/`select_mode` 行为契约不变。
- **FR-004**: 模拟行为 3 项（详情加载随机等待、人形滚动次数、鼠标移动概率）MUST 作为内部配置随档位（stable/balanced/extreme）下发，MUST NOT 出现在高级设置 UI。
- **FR-005**: 详情抓取（`scripts/boss/detail_scrape.py` 链路，含串行 `_scrape_one_detail` 与并行 `_scrape_detail_on_tab`）MUST 在页面加载完成后、JD 提取前执行模拟行为：随机等待（档位区间）、人形滚动（档位次数、随机距离、偶尔回滚）、按概率随机鼠标移动。
- **FR-006**: 模拟行为参数 MUST 经 CLI/in-process 翻译链路贯通（`scrape_details` 新参 → `--simulation-mode` → `fetch_details_batch` → `_build_detail_batch_command`）；未传参数时 MUST 零仿真、与现状一致。
- **FR-007**: 档位相关控件配色 MUST 为：稳定=绿、平衡=黄、极限=红、自定义=默认色。
- **FR-008**: 模式选择器正下方 MUST 有黄色警示区（一行字横幅、固定显示、不可关闭、无叉号）。
- **FR-009**: 极限档时警示区 MUST 显示"极高概率限流封号"。
- **FR-010**: 任务规模判定 MUST 改为新口径：总页数 <15 小 / 15~30 中 / >30 大（替换旧 9/49），后端 `classify_task_size`/`normalize_scope` 与前端 `classifyTaskSize` 同步。
- **FR-011**: 大任务（总页数 >30）时，任何档位（含稳定、自定义）警示区 MUST 显示"任务规模过大可能封号"。
- **FR-012**: 极限警告与大任务警告 MUST 合并显示于同一警示区、可同时出现；都不满足时警示区整体隐藏。
- **FR-013**: pages 输入范围 MUST 收紧为 1~200（前端范围与校验），后端 1~200 校验维持。
- **FR-014**: 发布后 MUST 保持用户当前档位选择不变（不重置 `advanced_config_state.active_selection`，不改写 custom 配置）。
- **FR-015**: README MUST 同步档位说明（三档数值、模拟行为、pages 范围、警示语义）。
- **FR-016**: 版本 MUST 做 minor 提升（`scripts/bump_version.py`），同步 pyproject.toml / webui/package.json / webui/package-lock.json / uv.lock / scripts/boss_cdp_raw.py / tests/test_desktop_shell.py / README 标题，并生成 CHANGELOG 条目。

### Key Entities

- **mode_configs（新模块，webui/mode_configs.py）**：三档 × 三规模数值表（一档一套、三规模同值）+ 三档模拟行为参数表 + 任务规模阈值常量（small_max=14, medium_max=30）。替代 execution_config.py 内 `_MODE_CONFIGS`（该文件已达 800 行上限，见 Constitution Check）。
- **detail_simulation（新模块，scripts/boss/detail_simulation.py）**：模拟行为参数解析与执行（随机等待、人形滚动、概率鼠标移动），参考 roadmap/boss-zhipin-scraper 的 `human_scroll` / `human_mouse_jitter` 实现。
- **ModeWarningBanner（新组件，webui/src/components/ModeWarningBanner.vue）**：黄色警示区，接收 `extremeWarning` / `largeTaskWarning` 两个布尔输入，固定展示、不可关闭。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 后端聚焦测试通过：`get_mode_config` 三档新数值、三规模同值、extreme=固化值、pages 不在快照、custom 解耦（改 custom 不影响 extreme）。
- **SC-002**: 规模口径测试通过：14→small、15→medium、30→medium、31→large、200→large、201 拒绝（后端与前端一致）。
- **SC-003**: 模拟行为测试通过：档位区间等待/滚动/鼠标概率断言（串行+并行路径）、CLI/in-process `--simulation-mode` 贯通、None 时零仿真回归。
- **SC-004**: 前端测试通过：警示区四种状态渲染、滑块配色、pages 范围 1~200、档位选择保持（custom 不重置）。
- **SC-005**: `npm run build` 通过；后端全量测试通过；仓库卫生检查（`tests.test_repo_hygiene` + hooks）通过。
- **SC-006**: README 档位章节存在且数值与冻结表一致；minor 版本各清单文件同步、CHANGELOG 更新。

## Verification Scope

- 功能交付：相关模块聚焦测试（test_execution_config、detail_scrape/simulation、source_boss_cdp_detail、settings/store_config、前端 discovery/DiscoveryView/ModeWarningBanner）+ 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查。
- 版本提升（minor）按根 `AGENTS.md` 收口规则执行（`scripts/bump_version.py` + CHANGELOG），随后提交/推送走收口验证（卫生测试、hooks、`git diff --check`、`git status`）。
- 不涉及打包/Release；用户端到端真跑验证（如档位实际生效、警示区实际显示）在交付后进行。

## Assumptions

- **A1（pages 与档位）**：`pages` 不进入模式配置快照（FR-009 维持）；冻结表中 pages 列为档位建议翻页数，仅用于文档与规模判定输入，档位切换不改动 pages 输入框的值。
- **A2（活动模式版本）**：数据库若存在活动模式版本（调优实验产物 `mode_config_versions`），三档新数值不自动改写其 matrix；新数值仅作用于无活动版本时的默认路径（`get_mode_config`）。若用户希望新数值覆盖旧 matrix，需另行操作（重新生成/应用版本）。
- **A3（模拟行为范围）**：模拟行为仅作用于 BOSS 详情抓取链路（`scripts/boss/detail_scrape.py`）；列表抓取与智联链路不在本期范围（用户需求仅点名 detail_scrape.py）。
- **A4（custom 与警告）**：极限警告仅在 extreme 档显示；大任务警告在全部档位（含 custom）显示。
- **A5（数值照表）**：冻结表中偏大数值（如稳定档 detail_interval=15s、detail_batch_cooldown=15s）按表原样落地，不做额外合理性调整。
- **A6（警示区交互）**：警示区为纯展示，不阻塞任何操作，不提供关闭/确认按钮。
- **A7（模式版本契约）**：`select_mode` / `create_mode_version` 的 3×3 matrix 契约与 `ExecutionConfigSnapshot` 校验不变，仅数据变化。
