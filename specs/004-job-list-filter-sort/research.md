# 调研记录：岗位列表筛选与排序

## 1. 现状调查（代码事实，2026-08-07 核验）

### 1.1 结果页数据流

```
GET /api/latest-pipeline-result → pipelineResult（父组件）
  → filterPipelineResultByPlatform(resultPlatformFilter)   // DiscoveryView.vue L384：平台筛选，影响 tab 计数
  → partitionPipelineResult(...)                           // discovery.ts L80：按 verdict 分 4 组
  → groups { matched / unmatched / uncertain / dropped }
  → currentJobs = groups[activeCategory]                   // DiscoveryView.vue L394
  → <JobWorkspace :jobs>                                   // DiscoveryView.vue L2189
  → visibleJobs = jobs.slice(0, visibleCount)              // JobWorkspace.vue：无限滚动，batchSize=30
```

- 平台筛选（全部/智联/BOSS）在**父组件**做，因为 4 个分类 tab 的计数必须联动（`resultTabs` 计数基于过滤后 groups）。
- `JobWorkspace` 是纯展示组件；`platformFilter` prop 只用于渲染「全部/智联/BOSS」档位 UI，过滤由父组件完成（JobWorkspace.vue 注释明确）。
- 列表头部结构：`[N 个岗位] [全部/智联/BOSS segment] [heading-actions slot（重抓按钮等）]`。

### 1.2 岗位数据字段（API 岗位对象合同，http-api.md）

统一岗位对象：`platform`、`platform_job_id`、`job_id`、`title`、`company`、`salary`、`location`、`experience`、`degree`、`jd`、`canonical_url`、`extra`。

| 筛选需求维度 | 现有字段 | 取值形态 | 可用性 |
|---|---|---|---|
| 薪资 | `salary` | 文本：「20-30K」「10-15K·13薪」「面议」「150-200元/天」 | ✅ 前端解析区间 |
| 工作经验 | `experience` | 文本：「1-3年」「3-5年」「经验不限」「应届生」 | ✅ 包含匹配 |
| 学历 | `degree` | 文本：「本科」「硕士」「大专」 | ✅ 包含匹配 |
| 福利 | 无 | — | ⚠️ 见 1.3 |
| 工作类型（全职/实习/兼职） | 无 | BOSS `skills` 文本偶含「实习」字样，非结构化 | ❌ 本期不做 |
| 排序-发布时间 | 无 | 原始抓取未归一化；`screening_runs.created_at` 是 run 时间 | ❌ 置灰 |
| 排序-匹配度 | 无 | `verdict` 仅三态（match/not_match/uncertain），`ai.py` 无连续评分 | ❌ 置灰 |

### 1.3 BOSS welfare 数据链路（本次后端改动的事实基础）

1. **抓取**：`scripts/boss_cdp_raw.py` L471 列表页输出 `welfare: (j.welfareList || []).join(' | ')`——**数据源已有**，格式为「五险一金 | 双休」管道分隔字符串。
2. **归一化**：`webui/source.py` L530 `_normalize_job_fields`（T133，BOSS 特有字段名 → 统一字段名）只处理 `job_id`/`source_url`/`company` 别名，**不提取 welfare**；键保留在 dict 中但无消费方。
3. **持久化**：`webui/store.py` 白名单字段 + `extra_json`（jobs 与 screening_results 表均有 `extra_json TEXT NOT NULL DEFAULT '{}'`，L2923/L2968）。extra 已全程持久化（`store.py` L2561-2606 写入，L168-222 读取）。
4. **展示快照**：`webui/pipeline_job_identity.py` `JobDisplay.extra: Mapping`（L128），`JobWorkspace.vue` `extraLabels`（L377-404）透传展示，`EXTRA_LABEL_MAP` 将已知键映射为中文。
5. **结论**：welfare 丢失在「归一化不提取 + 持久化不包含」。补齐点选在**归一化/展示快照组装层**（`source.py` 或 `pipeline_exec.py`），将 `welfare` 字符串拆为数组写入 `extra["welfare_list"]`；持久化与 API 透传零改动（extra 已全链路支持）。

### 1.4 前端现有可复用零件

- AI 筛选器的 `choice-chip` sentinel 交互（DiscoveryView.vue `filter-groups`：选中 sentinel 即清空该组，互斥）——「不限」交互沿用此模式。
- `pipeline_exec.py` `_job_salary_code`（L678-707）：纯文本薪资 → 档位映射的既有逻辑，可作前端解析规则的参考（前端为展示层，只需区间判断，不需要映射为 BOSS 档位码）。
- `BaseDialog.vue` 是居中模态（backdrop + focus trap），**不适用**锚定下拉浮层；需新增轻量浮层定位（锚定按钮 + 点击外部/ESC 关闭）。
- 深色主题变量集中在 `webui/src/styles/theme.css`（`--panel`、`--hair`、`--shadow` 等），浮层样式沿用。

## 2. 关键决策记录（grill 确认，2026-08-07）

| # | 决策 | 结论 | 理由 |
|---|---|---|---|
| D1 | Spec 范围 | **含后端 welfare 补齐** | 福利筛选是需求一部分；改动小（纯新增 extra 键）、无迁移、向后兼容 |
| D2 | 筛选是否联动 tab 计数 | **不联动** | 计数永远代表真实分类数；筛选是当前列表的临时收窄；实现不触碰父组件 |
| D3 | 状态生命周期 | **会话内保留，刷新/重搜重置** | 新结果岗位是新的，旧条件不一定合适；与 BOSS/智联网页行为一致；实现零成本 |
| D4 | 薪资「面议」处理 | **选任档位后隐藏；不归入任何档位** | 面议不代表低薪，藏起最干净；与 BOSS 网页一致 |
| D5 | 「不限」交互 | **sentinel 互斥，默认选中** | 与现有 AI 筛选器一致；「不限+其它」共存无意义 |
| D6 | 工作类型组 | **本期不显示** | 数据缺口；面板一整组不可用比排序菜单置灰两项更突兀 |
| D7 | 智联福利缺失提示 | **福利组下加灰字提示** | 一行字避免「智联岗位去哪了」的困惑 |
| D8 | 经验档位 | **不限/应届生/1-3年/3-5年/5-10年/10年以上**（严格包含匹配） | 覆盖实际数据形态；加档位是配置改动 |
| D9 | 筛空空态 | **「没有符合条件的岗位」+「清除筛选」按钮** | 筛空后一键恢复，少一步操作 |
| D10 | 排序菜单 | **综合/薪资最高/薪资最低可用；最新发布/匹配度最高置灰** | 数据缺口；配置位预留，后端补齐后去掉 disabled 即上线 |

## 3. 风险与边界

1. **薪资解析的文本形态不可控**：`salary` 是平台自由文本。解析规则限定为「先排除非 K 单位（元/天、日薪等）→ 提取 `\d+(\.\d+)?` 数字序列 → 首数为下限、最大值为上限」，忽略「·13薪」等后缀；「元/天」实习计价与「面议」不落入任何档位。规则写进合同 §3 并有单测覆盖。
2. **智联无福利数据**：福利筛选对智联岗位自然过滤，属预期行为（D7 提示 + 空态兜底）。
3. **旧结果无 `welfare_list`**：缺失即不满足，不报错（向后兼容，验收场景 US4-4）。
4. **浮层裁剪风险**：浮层锚定在 `job-list-heading`（列表内部滚动容器 `.job-list` 之外），需验证无 `overflow: hidden` 裁剪；窄屏（390px）下按钮退化为纯图标，避免头部换行挤压。
5. **无限滚动与筛选排序的交互**：筛选/排序改变后 `visibleCount` 需重置回 `batchSize`（现有 `watch(jobs, ...)` 的签名比较不感知纯展示层变化，需在筛选/排序变化时手动重置）；新结果加载的重置走 `resultEpoch` 信号（合同 §6），不用 jobs 内容签名判断（切分类/切平台同样改变 jobs，会误重置）。此点写入合同与任务。
6. **排序稳定性**：薪资排序需稳定（相同薪资保持原相对顺序）；「面议」两种排序均沉底。