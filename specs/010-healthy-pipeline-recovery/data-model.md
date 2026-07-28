# Data Model: 健康流程补救与优化

**Date**: 2026-07-28

## 1. 统一任务状态机

### 1.1 状态定义

| 状态 | 含义 | 互斥 |
|---|---|---|
| `waiting` | 任务已创建，尚未开始 | 是 |
| `running` | 任务正在执行某个阶段 | 是 |
| `paused` | 因系统性阻断暂停，等待用户处理 | 是 |
| `completed` | 所有岗位已处理且无未开始，无系统性阻断 | 是 |
| `completed_with_pending` | 所有岗位已处理，少量独立失败在待确认 | 是 |
| `failed` | 内部错误导致无法继续（非系统性阻断） | 是 |
| `cancelled` | 用户主动取消，保留已有结果 | 是 |

### 1.2 合法状态转换

```
waiting ──→ running ──→ completed
              │      ──→ completed_with_pending
              │      ──→ failed
              ↓
            paused ──→ running (用户点继续且阻断解除)
              │      ──→ cancelled (用户取消)
              ──→ failed (内部错误)

running ──→ cancelled (用户取消)
paused  ──→ cancelled (用户取消)
waiting ──→ cancelled (用户取消)
```

**非法转换**（必须拒绝）：
- `completed` → `running`（已完成不能恢复）
- `cancelled` → 任何状态（已取消不能恢复）
- `waiting` → `paused`（未开始不能暂停）
- `failed` → `running`（内部错误需人工排查）

### 1.3 完成判定规则

```
IF 存在未开始岗位 OR 存在系统性阻断:
    status = paused
ELIF 存在待确认岗位 (独立失败):
    status = completed_with_pending
ELSE:
    status = completed
```

**守恒约束**：`matched + mismatched + pending + dropped == source_count`

## 2. 阶段定义

| 阶段 | 标识 | 说明 |
|---|---|---|
| 列表抓取 | `scrape` | keyword×city 组合抓取岗位列表 |
| AI 粗筛 | `ai_rough` | 对列表结果做粗筛，移除明显不匹配 |
| JD 详情 | `jd_detail` | 逐个抓取岗位详情页 JD |
| AI 精筛 | `ai_fine` | 对有 JD 的岗位做精筛匹配判定 |

**阶段顺序**：`scrape → ai_rough → jd_detail → ai_fine`

**暂停可发生在任何阶段**。继续时从暂停的阶段恢复，跳过该阶段已完成项。

## 3. 统一错误分类码表

| failed_code | 影响范围 | 是否阻断 | 是否可重试 | 用户可读原因 | 继续条件 |
|---|---|---|---|---|---|
| `captcha_required` | 系统性 | 是 | 是 | 触发验证码/滑块，需手动完成 | 用户完成验证码后点继续 |
| `login_expired` | 系统性 | 是 | 是 | BOSS 登录已失效，需重新登录 | 用户重新登录后点继续 |
| `ai_rate_limited` | 系统性 | 是 | 是 | AI 服务限流，请求过于频繁 | 等待限流解除后点继续 |
| `ai_quota_exhausted` | 系统性 | 是 | 否 | AI 额度已耗尽 | 充值或更换密钥后点继续 |
| `ai_key_invalid` | 系统性 | 是 | 否 | AI 密钥失效或鉴权失败 | 更换有效密钥后点继续 |
| `ai_network_error` | 系统性 | 是 | 是 | AI 网络或服务故障 | 网络恢复后点继续 |
| `ip_risk_control` | 系统性 | 是 | 是 | IP 级风控拦截 | 更换网络或等待后点继续 |
| `cdp_unavailable` | 系统性 | 是 | 是 | 连不上调试浏览器 | 启动 Chrome 调试端口后点继续 |
| `job_offline` | 独立岗位 | 否 | 否 | 岗位已下架 | 无需继续，该岗位进入待确认 |
| `detail_timeout` | 独立岗位 | 否 | 是 | 单岗位详情抓取超时 | 可单条补抓重试 |
| `detail_invalid` | 独立岗位 | 否 | 否 | 详情结构无效（登录墙/导航壳/空壳） | 可单条补抓 |
| `ai_missing_job` | 独立岗位 | 否 | 是 | AI 漏回单个岗位判定 | 可单条补抓重试 |
| `internal_error` | 系统性 | 是 | 否 | 内部状态或持久化错误 | 需人工排查日志 |

**硬停止码集合**（命中即暂停整个任务）：
```python
SYSTEMIC_BLOCK_CODES = {
    "captcha_required", "login_expired", "ai_rate_limited",
    "ai_quota_exhausted", "ai_key_invalid", "ai_network_error",
    "ip_risk_control", "cdp_unavailable", "internal_error"
}
```

**独立失败码集合**（仅该岗位进待确认，不阻断）：
```python
INDEPENDENT_FAILURE_CODES = {
    "job_offline", "detail_timeout", "detail_invalid", "ai_missing_job"
}
```

## 4. 实体定义

### 4.1 流程任务（screening_runs 扩展）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 任务身份 |
| status | TEXT | 统一状态机取值 |
| current_stage | TEXT | 当前阶段（scrape/ai_rough/jd_detail/ai_fine） |
| source_count | INTEGER | 岗位总数 |
| processed_count | INTEGER | 已处理数 |
| match_count | INTEGER | 匹配数 |
| mismatch_count | INTEGER | 不匹配数 |
| pending_count | INTEGER | 待确认数（实时更新） |
| dropped_count | INTEGER | 粗筛移除数 |
| error_code | TEXT | 暂停时的系统性阻断码 |
| error_reason | TEXT | 用户可读暂停原因 |
| backend_version | TEXT | 启动时的后端版本 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

### 4.2 岗位处理项（screening_results，结构修正）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 岗位 ID |
| run_id | TEXT FK | 所属任务 |
| verdict | TEXT | **枚举值** `match`/`mismatch`/`uncertain`（不再存 JSON） |
| verdict_reason | TEXT | 具体原因（失败时为 failed_code 对应的可读原因） |
| failed_code | TEXT | 失败码（独立岗位失败时填） |
| failed_stage | TEXT | 失败阶段 |
| retryable | INTEGER | 是否可重试 |
| attempts | INTEGER | 尝试次数 |
| jd | TEXT | JD 正文（成功时） |
| caveats_json | TEXT | 注意事项 JSON |
| is_dropped | INTEGER | 是否粗筛移除 |
| created_at | TEXT | 创建时间 |

**结构异常修正**：现有 762 条 verdict 字段存的是 JSON `{"verdict":"match",...}`，恢复时解析提取 `verdict` 值写入枚举字段，`reason` 写入 `verdict_reason`，`caveats` 写入 `caveats_json`。

### 4.3 待确认岗位（screening_pending_results，启用）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 岗位 ID |
| run_id | TEXT FK | 所属任务 |
| job_id | TEXT | 岗位 ID |
| failure_stage | TEXT | 失败阶段 |
| failed_code | TEXT | 失败码 |
| retryable | INTEGER | 是否可重试 |
| attempts | INTEGER | 尝试次数 |
| last_failed_at | TEXT | 最后失败时间 |
| origin_zone | TEXT | 来源区域 |
| ai_payload_json | TEXT | AI 原始返回（如适用） |
| created_at | TEXT | 创建时间 |

### 4.4 断点检查点（pipeline_checkpoints，新增）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | 自增 |
| run_id | TEXT FK | 所属任务 |
| stage | TEXT | 阶段 |
| completed_keys_json | TEXT | 已完成项的 key 列表 JSON |
| saved_at | TEXT | 保存时间 |

**写入时机**：每次暂停时保存当前阶段的已完成项。
**读取时机**：继续时加载对应阶段的 checkpoint，跳过已完成项。
**key 含义**：scrape 阶段=`"keyword|city"` 组合；jd_detail 阶段=`job_id`；ai_rough/ai_fine 阶段=`job_id`。

### 4.5 流程事件（task_logs 复用 + 扩展）

复用 task_logs 表，新增字段（或用 line 的 JSON 存储）：

| 事件类型 | 说明 |
|---|---|
| `stage_start` | 阶段开始 |
| `stage_complete` | 阶段完成 |
| `job_success` | 岗位成功 |
| `job_fail` | 岗位失败（含 failed_code） |
| `pause` | 任务暂停（含 error_code + 原因） |
| `resume` | 任务继续 |
| `cancel` | 任务取消 |
| `block_check` | 继续前阻断检查结果 |

## 5. 历史恢复数据模型（2026-07-28 基于数据库实测修正）

### 5.1 两个 run 的 50 条区别（核心修正）

事故跨两个 run，各有 50 条，性质完全不同：

| run | 50 条性质 | verdict 格式 | 有无有效判定 | 恢复动作 |
|---|---|---|---|---|
| 15847d27（粗筛）| 17 match + 33 not_match | 纯字符串（非 JSON）| **有** | 格式统一（字符串→枚举），**不调 AI** |
| e6250f0e（精筛）| 50 uncertain | JSON `{"verdict":"uncertain","reason":"AI 响应超时"}` | **无** | 交给新流程**重新调 AI** |

**15847d27 的 50 条识别**：
```sql
SELECT id, verdict FROM screening_results
WHERE run_id = '15847d27-7419-4f01-ae09-9e4c9e2641bb'
  AND verdict IN ('match', 'not_match')  -- 纯字符串，非 JSON
```
- `verdict == 'match'` → 17 条，统一为枚举 `match`
- `verdict == 'not_match'` → 33 条，统一为枚举 `mismatch`

**e6250f0e 的 50 条识别**：
```sql
SELECT id, verdict FROM screening_results
WHERE run_id = 'e6250f0ed794492180269de050bfd41a'
  AND json_extract(verdict, '$.verdict') = 'uncertain'
```
- 50 条 uncertain（AI 超时），无有效判定，交给新流程重新调 AI

### 5.2 646 条待补救识别

646 条 = 1408（粗筛 kept）- 762（精筛处理）。在 15847d27 screening_results 中作为 `uncertain`（纯字符串）。

```sql
-- 15847d27 中 kept 但未进精筛的岗位
SELECT id FROM screening_results
WHERE run_id = '15847d27-7419-4f01-ae09-9e4c9e2641bb'
  AND verdict = 'uncertain'
  AND id NOT IN (SELECT id FROM screening_results WHERE run_id = 'e6250f0ed794492180269de050bfd41a')
```

**646 条是否可进一步区分 30/8/608**：
- 数据库中**无 failed_code / failure_stage 字段记录**这 646 条的具体失败原因
- task_logs 表用 task_id（非 run_id），且无对应记录
- 30/8/608 的区分**无法从现有数据库可靠推断**
- **降级方案**：646 条统一标 `failed_code='historical_reason_unavailable'`
  和 `failure_stage='jd_detail'`，保存“旧流程未保存具体失败原因”、可确认的历史证据与
  `next_action='recrawl_jd'`，再由新流程逐条抓取时实时分类（下架/超时/无效/成功）。
  不猜测补齐 30/8/608（FR-045）。

### 5.3 e6250f0e 的 762 条 JD 回填

e6250f0e 的 762 条 screening_results **jd_len 全部=0**，JD 正文只在 job-result 的 `pipeline_detail_*.json` 文件中（337 个文件）。

恢复时需：
1. 从 `pipeline_detail_<job_id>.json` 读取 JD 正文
2. 回填到 screening_results.jd 字段
3. 无法回填的（文件不存在）标 `detail_invalid`

### 5.4 守恒关系（修正后）

```
1926 (source_count)
= 518 (dropped, 粗筛移除)
+ 1408 (kept, 粗筛保留)
  = 17 (15847d27 match) + 33 (15847d27 not_match) + 1358 (15847d27 uncertain)
    = 762 (e6250f0e 精筛处理) + 646 (未进精筛)
      762 = 198 (match) + 514 (not_match) + 50 (uncertain AI 超时)
      646 = 待补救（无法预区分 30/8/608，由新流程实时分类）
```

恢复后（15847d27 的 50 条格式统一 + e6250f0e 的 50 uncertain 重新判定 + 646 条新流程处理）：
```
1926 = 518 (dropped) + 215 (match: 198+17) + 547 (mismatch: 514+33) + 646 (pending)
       OR (646 + 50 补救完成后)
1926 = 518 (dropped) + 215+? (match) + 547+? (mismatch) + ? (remaining pending)
```

### 5.5 预演输出项

只读预演必须输出并核对：
- 15847d27 run：17 match + 33 not_match + 1876 uncertain（含 518 dropped + 1358 kept-uncertain）
- e6250f0e run：198 match + 514 not_match + 50 uncertain（AI 超时）
- 646 条 = 15847d27 kept-uncertain 中未进 e6250f0e 的部分
- 696 = 646 + 50（e6250f0e uncertain）
- 762 条 jd_len=0，JD 在 337 个 pipeline_detail 文件中
- 守恒关系全部满足
- 预演不写入正式数据库
