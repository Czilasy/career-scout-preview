# Data Model: B096 V2

## 1. UnifiedFilterDraft（统一条件草稿）

前端内存实体；不属于任一平台。

```text
mappingVersion: string
values:
  salary: string[]
  experience: string[]
  degree: string[]
  industry: string[]
  scale: string[]
  recruiter_activity: string[]
```

- 选项使用 V2 自有稳定 ID，不使用任一平台 code。
- 空数组表示不限；显式“不限”归一化为空数组。
- 简历分析只写初始值；用户修改后即为权威值。

## 2. PlatformFilterDraft（平台最终条件草稿）

```text
boss: Record<fieldKey, string[]>
zhilian: Record<fieldKey, string[]>
```

- 值是当前平台 schema 的稳定 code。
- 共同字段由统一草稿首次投影；用户可平台微调。
- BOSS `stage`、智联 `company_nature` 只存在于对应平台草稿。
- 修改统一字段时只替换两平台该共同字段。

## 3. ConditionSnapshotV2（条件快照）

```text
snapshotVersion: 2
mappingVersion: string
unifiedValues: UnifiedFilterDraft.values
platformValues:
  boss: Record<string, string[]>
  zhilian: Record<string, string[]>
overrides:
  boss: Record<string, string[]>
  zhilian: Record<string, string[]>
exclusiveValues:
  boss: { stage?: string[] }
  zhilian: { company_nature?: string[] }
```

`overrides` 只记录平台最终值与统一映射值不同的共同字段；恢复以 `platformValues` 为权威，不重新应用映射。

## 4. SearchPackage V2

现有字段全部保留，新增：

```text
payload_version: 1 | 2
condition_snapshot_json: JSON object, default {}
```

- 新保存：version 2 + 完整 ConditionSnapshotV2。
- 旧保存：version 1 + `{}`；继续可读，恢复时六类不限、平台专属空白。
- JSON 损坏、version 2 缺关键字段或值类型非法：整包不可用，不部分回填。

## 5. Flow 与 Track（复用）

不新增表和列。

```text
Flow
  id, profile_id, selection, start_key, timestamps
  tracks[2]

Track
  platform
  scrape_run_id
  screen_run_id
  result_run_id
  status
  stage
  confirmed_filters_snapshot  # V2 信封；V1 可为纯平台字段对象
  submission_snapshot
```

- 抓取展示 run：`scrape_run_id`。
- AI 展示 run：`screen_run_id`。
- 结果签名：`result_run_id`；失败但有部分岗位时由 Flow 结果响应补充。
- 一条 Track 的状态变化不得写兄弟 Track。

## 6. FlowPresentationState（前端派生）

```text
flowId: string
hydrated: boolean
unlockedSteps: Set<upload|search|screen|results>
highestUnlocked: StepId
manualHold: boolean
scrapeItems: ProgressItem[]
screenItems: ProgressItem[]
seenResultSignatures: Set<string>
```

`ProgressItem` 包含 platform、track、runId、真实 TaskSnapshot 和 enteredAt。

不变式：

1. 解锁集合在同一 Flow 内只增不减。
2. 首次水合不自动跳页、不发结果加入通知。
3. `manualHold=true` 时状态更新不改 `activeStep`。
4. 03 不含尚未创建 AI run 的平台。
5. 同阶段同平台最多一个进度项。
6. 结果通知签名每个运行时只消费一次。

## 7. 迁移

- Migration 040：`search_packages` 新增 `condition_snapshot_json`。
- 新库先执行旧建表迁移，再执行 040，结构与升级库一致。
- 不改写旧配置内容，不重算历史映射。
