# Data Model: 步骤页切换状态交接安全

**Spec**: `specs/041-step-switch-state-safety/spec.md` | **Date**: 2026-09-12

## 核心实体

### 1. 轮次身份键（SceneIdentity）

现场归属的唯一标识，身份变则清、身份不变则留（spec FR-004/FR-005）。

```typescript
interface SceneIdentity {
  profileId: string;       // 求职画像 ID（切画像 → 清）
  runEpoch: string;        // 轮次标识（开新轮 → 换发；跨刷新从会话存档取回同一值）
  platform: Platform;      // 平台（切平台 → 隔离）
}
```

- 身份键序列化：`<profileId>::<runEpoch>::<platform>`。
- **轮次标识来源（返工修订）**：`runEpoch` 是业务轮次的稳定标识，由现场存档生成一次并持久化
  （`career-scout-round-epoch:<profileId>`）；同一轮从上传、抓取、筛选到结果完成期间不变。
  禁止用抓取/筛选/补抓任务号或结果 `source_run_id` 充当轮次身份——它们随阶段推进变化，
  会把"任务推进"误判成"换了新现场"并恢复成默认空现场。
- 三主体变更场景（spec FR-005/FR-006/FR-007/FR-008）：切画像清旧画像一切、开新轮旧轮降级
  （其查看现场保留）新轮回默认、切平台草稿隔离。
- 其他场景（切页/翻历史/刷新/后台跑完冒泡/灵动岛跳转/任务阶段推进）身份不变 → 不清。

### 2. 页面内现场（SceneSnapshot）

记录在某一页面内部的状态，绑在 SceneIdentity 上。

```typescript
interface PageScene {
  // 页面二（search）
  profileInputHeight: number | null;        // 画像框已撑开高度
  profileInputWidth: number | null;         // 算出该高度时的输入框宽度（宽度变即重算）
  profileInputContent: string;              // 算出该高度时的文本内容（内容变即重算）
  cityPanels: Record<string, CityPanelScene>; // 按城市名的区县面板现场
  cardScrollTops: Record<string, number>;    // 折叠卡片滚动位置

  // 页面四（results）
  sortKey: SortKey;                          // 排序方式
  listFilterDraft: FilterState;              // 列表展示筛选草稿（与 03 页 filterValues 区别）
  visibleCount: number;                      // 已加载批次
  selectedJobKey: string | null;             // 当前选中岗位（按身份跟随）
  userSelectedDetail: boolean;               // 是否手动选过
  detailOpen: boolean;                       // 详情开合
  jdScrollTop: number;                       // JD 滚动
  listScrollTop: number;                     // 列表滚动
}

interface CityPanelScene {
  open: boolean;
  districts: DistrictCatalog;               // 已加载区县目录（缓存复用）
  cityCode: string;
}

interface SceneSnapshot {
  current: PageScene;                        // 当前轮现场
  history: Record<string, PageScene>;       // 历史轮查看现场（懒存：只存翻过的 runId）
}
```

### 3. 历史轮查看现场

历史轮次的轻量查看状态，只存实际翻过的轮次（spec FR-002/Assumptions 懒存）。

- **复用 `PageScene` 结构**（返工修订：不再单独定义 `HistoryViewScene` 类型——原类型无任何调用方，
  属零引用死代码）：历史轮现场同样落 `PageScene`，只是写入时把 `listFilterDraft` 归零——
  历史轮无未确认草稿，只展示已定筛选状态（spec FR-002）。
- 存档键：`history[结果 run id]`；开新轮归档与历史轮浏览共用同一键空间。

### 4. 灵动岛落点目标（IslandNavTarget）

```typescript
type IslandNavTarget =
  | "home"          // 空闲/分析中 → 01
  | "task-scrape"  // 运行中抓取/暂停在抓取/出错在抓取 → 当前轮 02
  | "task-screen"  // 运行中筛选/暂停在筛选/出错在筛选 → 当前轮 03
  | "results"      // 跑完 → 当前轮 04
  | "reminders";   // 投递提醒 → 提醒抽屉
```

派生规则（spec FR-010）：
- 空闲（无任务无结果）→ home
- 分析中 → home（显示分析中）
- 运行中抓取 → task-scrape
- 运行中筛选 → task-screen
- 跑完（有/无待确认）→ results
- 暂停 → 卡住的那步页（抓取中卡→task-scrape，筛选中卡→task-screen，按真实进度）
- 出错 → 同暂停，按真实进度

### 5. 简历分析状态（ResumeAnalysisState）

```typescript
type ResumeAnalysisPhase = "idle" | "analyzing" | "succeeded" | "failed";

interface ResumeAnalysisState {
  phase: ResumeAnalysisPhase;
  selectedFile: File | null;
  error: string;                             // 真实失败原因（不误报）
}
```

落点规则（spec FR-019/FR-020）：
- 用户回当前流程：succeeded → 02；analyzing → 01 显示分析中；failed → 01 显示真实失败。
- 分析完成自动进 02（仅当用户在 01 或主动回当前流程）。

## 状态转换

### 现场存档生命周期

```text
身份不变（切页/翻历史/刷新/后台冒泡）
  → 读存档恢复现场（瞬时，内存态）
  → 现场变化时写存档（防抖）
  → 刷新后从 sessionStorage 恢复

身份变（切画像/开新轮/切平台）
  → 切画像：清 current + 清 history + 清城市草稿（新画像干净态）
  → 开新轮：current 降级为 history[结果 run id]（保留查看现场），再换发新轮次身份，
    new current 回默认
  → 切平台：按平台键隔离（不互串）
```

### 灵动岛点击落点转换

```text
点胶囊 / 通知行
  → 派生 IslandNavTarget（按真实进度）
  → 若在历史模式：先 returnToLatest（历史现场原地保留）再落目标页
  → 若占位中：等加载完执行 / 明确不响应但放行下一次（不吞不锁）
  → 落目标页（硬红线：不清成空白上传页）
```

## 持久化

### sessionStorage（复用既有通道）

- key：`career-scout-workflow:<profileId>`（既有，扩展纳入 PageScene）。
- 现场载荷：`pageScene = { version: 2, current, history, runIds }`；`runIds` 是「轮次身份 → 该轮结果 run id」映射，供开新轮归档到历史轮键。
- 轮次身份单独存档：`career-scout-round-epoch:<profileId>`（返工修订），保证刷新后接回同一轮次身份。
- 版本号：`WORKFLOW_STATE_VERSION` 既有 =1，本 Spec 升至 =2（现场字段新增）。
- 防完成态误恢复：沿用 `isCompletedWorkflowSnapshot`（useDiscoveryWorkflow L29-41）。
- 只持久化「轮次级」现场（keywords/cityText/filterValues/profileSummary 既有 + PageScene 新增）；纯 DOM 滚动用内存态（刷新后从 sessionStorage 的 listScrollTop 恢复，精度可接受）。

### 内存态（模块级单例）

- `useDiscoverySceneState` 内部 `Map<SceneIdentity, SceneSnapshot>` 模块级单例，跨组件卸载存活。
- 历史轮 `history` 子表懒存（只存翻过的 runId），上限受用户翻看行为约束（不无限膨胀，spec Assumptions）。

### 后端

- 不新增表、不新增接口。
- 复用既有 `/api/latest-running-task`、`/api/latest-pipeline-result`、`/api/result-history*` 恢复轮次级现场。
- 页面内 UI 现场不进后端（前端 sessionStorage + 内存态）。

## 验证规则

- 现场身份键：`SceneIdentity` 三个字段任一变即视为身份变（清或隔离）。
- 选中跟随：`selectedJobKey` 用 `platform:platform_job_id` 双 ID（沿用 JobWorkspace `jobKey` L195-203）；重抓后按身份跟随，岗位消失回第一条并提示（spec 边缘场景）。
- resultEpoch 语义：新结果重置列表现场、切分类/切平台保留（沿用 JobWorkspace L81-89 watch）；现场存档不得破坏此判据。
- 历史轮无草稿：`HistoryViewScene` 无 `listFilterDraft`，只展示该轮已定筛选状态（spec FR-002）。
