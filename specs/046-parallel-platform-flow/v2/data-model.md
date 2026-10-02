# Data Model: B096 V2

## 2026-10-02 实例与组合边界澄清

- 复用现有 Flow、Track 与 run 标识表达独立实例，不因本次澄清新增持久实体或另一套任务状态。
- 条件、任务现场、动作忙态、错误与结果以实例标识隔离；同一业务逻辑消费对应上下文。组合模式不能把两条线写入一份全局“当前任务”。
- Flow 保存共同归属，FlowPresentationState 负责页面现场与合流协调；实例状态事实不由页面投影另行创造。
- 下文数据结构属于既有 V2 设计。本次只修对应实例的共享动作与结果投影，不重新建表、不重置数据、不重算历史快照。

### 本次落地的实例动作上下文

- 临时上下文固定 `flowId / trackId / runId / kind`，调用前和批次选择确认时复核身份；不新增数据库字段。
- `busyActions` 按实例保存，结束保存的共享入口接收实例 busy 和保存后回调；回调重新读取当前 Flow 完整结果，单轨响应不写共同结果。
- 批次弹窗只有一份，读取固定实例快照；兄弟实例变化不使选择丢失，目标批次结束或身份失效时关闭。
- Flow 操作携带 `expected_run_id`，后端在任何操作前核对当前阶段 run；旧阶段请求冲突返回 409。
- 真正 failed Track 为终态；既有可恢复失败仍由 task-state 投影为 paused 并遵守原恢复门禁，不新增失败重试策略。

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
