# JobSource Adapter 合同

**版本**：`job-source-v1`  
**实现者**：`BossCdpSource`、`ZhilianCdpSource`、测试用 `FakeJobSource`

## 职责边界

JobSource 只负责平台访问和字段归一化，不持久化业务数据、不推进 run 状态、不决定跨阶段重试，也不执行 AI 筛选。列表搜索输入只包含关键词、城市解析快照和页数；薪资、经验、学历、行业、公司规模、融资阶段或公司性质均不得进入 adapter 列表参数。

```python
class JobSource(Protocol):
    platform: str

    def preflight(self) -> SourceOutcome: ...
    def fetch_list(self, plan_item: dict) -> SourceOutcome: ...
    def fetch_detail(
        self, job: dict, *, detail_output_path: str | None = None
    ) -> SourceOutcome: ...
    def fetch_details_batch(
        self, jobs: list[dict], **bounded_options
    ) -> dict[str, SourceOutcome]: ...
```

adapter 验证冻结输入，调用对应平台 scraper，校验 artifact，再返回安全的类型化结果。编排层根据本文错误矩阵更新 run 和单项状态。

## 构造合同

factory 只接受任务创建时冻结的运行配置：

| 输入 | 规则 |
| --- | --- |
| `platform` | 必须与 adapter 注册键一致 |
| `browser_account` | 已存在的账号 ID |
| `cdp_port` | 正整数，所有 list/detail/batch subprocess 显式透传 |
| `profile_key` | 必须等于 `<platform>:<browser_account>` |
| `profile_dir` | 后端从平台和账号确定性解析的本地路径，不写日志、数据库或 API |
| `artifact_root` | 所有输入输出 artifact 必须位于该 run 的受控根目录内 |
| `timeout_seconds` | 有界正整数 |
| `cancel_event` | 沿用现有取消合同 |
| `breaker` | 每个运行上下文独立的 `SourceCircuitBreaker` |

任何方法不得回退读取全局当前平台、当前 UI 选择、活动账号或默认端口。BOSS 也必须显式接收冻结的 CDP 端口。

## SourceOutcome

普通成功：

```json
{
  "ok": true,
  "jobs": [{"platform": "zhilian", "platform_job_id": "platform-stable-id"}],
  "detail": {},
  "empty_result": false,
  "empty_evidence": null,
  "failed_code": null,
  "failed_reason": "",
  "safe_log": "platform=zhilian stage=list job_count=1",
  "input_hash": "sha256-hex"
}
```

真实空搜索：

```json
{
  "ok": true,
  "jobs": [],
  "detail": {},
  "empty_result": true,
  "empty_evidence": {
    "kind": "explicit_empty_state",
    "fixture_version": "zhilian-list-v1",
    "marker": "normalized-empty-state"
  },
  "failed_code": null,
  "failed_reason": "",
  "safe_log": "platform=zhilian stage=list empty_result=1",
  "input_hash": "sha256-hex"
}
```

失败：

```json
{
  "ok": false,
  "jobs": [],
  "detail": {},
  "empty_result": false,
  "empty_evidence": null,
  "failed_code": "source_login_required",
  "failed_reason": "需要登录智联招聘",
  "safe_log": "platform=zhilian stage=preflight login_required=1",
  "input_hash": "sha256-hex"
}
```

`ok=true` 且 `jobs=[]` 只允许 `empty_result=true`、登录和结构检查已通过、`empty_evidence` 可由当前版本 fixture 识别。零卡片、选择器异常或网络空响应本身都不是空结果证据。

### 编排持久化门槛

JobSource 不直接写数据库。编排层收到 list outcome 后，必须先在同一 run 下追加一条 `screening_source_attempts`，再更新组合完成键、run 进度或结果 snapshot：

- 非空成功写 `outcome_kind=non_empty`、非零 `job_count` 和同一 `input_hash`；
- 真实空成功写 `outcome_kind=empty`、`job_count=0` 和脱敏 `empty_evidence_json`；
- 重试耗尽的列表失败写 `outcome_kind=failed` 与稳定错误码；
- 平台级阻断写 `outcome_kind=paused` 与稳定错误码，然后才把 run 转为 paused；
- 持久化或 input hash 校验失败时不得推进 run、发布成功进度或生成结果 snapshot。

状态、进度与历史结果只按每个 `run_id/combo_key` 最大 `attempt_no` 汇总；不得从零岗位、日志文本或已过期内存 outcome 重建 empty。adapter 的 `empty_evidence` 只能包含 fixture 版本、证据种类、稳定 marker 与脱敏标记，不能包含页面正文、Cookie、JD 或本地路径。

允许的失败码沿用 `SAFE_FAILURE_CODES`：

- `source_cdp_unavailable`
- `source_login_required`
- `source_unreachable`
- `source_blocked`
- `source_not_found`
- `source_invalid_output`
- `source_input_drift`
- `source_timeout`
- `source_unknown_error`
- `source_verification_required`
- `source_rate_limited`

## 冻结错误矩阵

| 条件 | 适用阶段 | adapter 错误码 | 编排结果 |
| --- | --- | --- | --- |
| 未登录或登录墙 | preflight/list/detail/batch | `source_login_required` | 平台级 `paused` |
| EdgeOne、验证码或人机验证 | preflight/list/detail/batch | `source_verification_required` | 平台级 `paused` |
| 明确限流 | preflight/list/detail/batch | `source_rate_limited` | 平台级 `paused` |
| 平台封禁或明确拒绝访问 | preflight/list/detail/batch | `source_blocked` | 平台级 `paused` |
| 冻结 CDP 不可用或 profile 不匹配 | preflight/执行前 | `source_cdp_unavailable` | 平台级 `paused` |
| 网络连接失败 | preflight/list | `source_unreachable` | 有限重试耗尽后 run `failed` |
| 网络或页面等待超时 | preflight/list | `source_timeout` | 有限重试耗尽后 run `failed` |
| 输入或 artifact 摘要漂移 | 任意 | `source_input_drift` | run `failed`，禁止导入产物 |
| 全局关键结构不兼容 | preflight/list | `source_invalid_output` | run `failed` |
| 单岗位下架或不存在 | detail/batch item | `source_not_found` | 单项失败/待确认，其它岗位继续 |
| 单岗位详情超时 | detail/batch item | `source_timeout` | 单项失败/待确认，其它岗位继续 |
| 可归属到单岗位的详情结构异常 | detail/batch item | `source_invalid_output` | 单项失败/待确认，其它岗位继续 |
| 批量详情连续出现平台级 signal 并触发熔断 | batch | 对应平台级 source 码 | run `paused`，保留已完成项 |
| 无法分类的列表/全局错误 | preflight/list | `source_unknown_error` | run `failed` |
| 无法分类但可归属到单岗位的错误 | detail/batch item | `source_unknown_error` | 单项失败/待确认，其它岗位继续 |

若网络失败页面同时具有可验证的登录墙、验证、限流或封禁证据，必须返回对应的具体平台级错误码，不能用 `source_unreachable` 代替。所有平台级暂停都由编排层执行 `queued -> running -> paused`；adapter 本身不修改状态。

## preflight

每个新运行及每次继续运行前执行一次。成功条件：

1. 冻结 CDP 端口可用并返回有效浏览器信息。
2. 活动 profile 与冻结 `profile_key` 对应。
3. 当前平台可访问且登录有效。
4. 页面不存在验证、限流或封禁信号。
5. 用于判断登录、空状态和关键结构的当前 fixture 版本可用。

任一条件失败时必须返回错误矩阵中的单一确定结果。preflight 不允许使用“失败或暂停”一类二选一语义。

## fetch_list

`plan_item` 必填字段：

```json
{
  "platform": "zhilian",
  "keyword": "Python 后端",
  "city": {
    "name": "上海",
    "platform_code": "verified-city-code",
    "mapping_version": 1,
    "mapping_label": "上海"
  },
  "target_pages": 1,
  "input_hash": "sha256-hex",
  "list_output_path": "artifact path under run root"
}
```

规则：

- `platform` 必须与 adapter 一致。
- 城市规范名、平台码、映射版本和标签必须与 run 冻结快照一致；智联全国的平台码固定为 `jl0`。
- 缺少当前平台城市映射时返回输入校验失败，不得复用另一平台城市码。
- 输入不得出现 `source_filters`、AI 筛选字段、融资阶段或公司性质。
- `input_hash` 覆盖平台、关键词、完整城市解析快照和页数；输出 artifact 导入前重新计算并校验。
- 关键词编码由智联页面搜索行为产生，不自行复制不透明编码算法。
- 每页至少验证登录状态、关键列表结构和分页/终页信号；关键容器缺失不能当空结果。
- 空结果必须满足 `SourceOutcome` 的明确证据合同。

统一列表岗位：

```json
{
  "platform": "zhilian",
  "platform_job_id": "platform-stable-id",
  "title": "Python 后端工程师",
  "company": "示例公司",
  "salary": "20-30K",
  "location": "上海",
  "experience": "3-5年",
  "degree": "本科",
  "source_url": "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
  "canonical_url": "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
  "extra": {"company_nature_label": "民营"}
}
```

`platform_job_id` 是平台原始身份，不得放入 `job_id`。缺失页面字段保持空字符串或省略可选 extra；不得填入经验默认值、薪资估算、公司性质猜测或 AI 筛选选项编码。`extra` 只保留已归一化的非敏感页面字段。

## fetch_detail

输入必须含 `platform`、`platform_job_id` 和经过平台注册规则规范化的 `canonical_url`。输出必须归属到同一平台岗位身份：

```json
{
  "platform": "zhilian",
  "platform_job_id": "platform-stable-id",
  "source_url": "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
  "canonical_url": "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
  "jd": "页面可见的岗位描述",
  "experience": "3-5年",
  "degree": "本科",
  "extra": {}
}
```

详情可刷新列表字段，但不得改变 `platform` 或 `platform_job_id`。详情缺失、下架、单项解析失败和平台级阻断按错误矩阵区分。`jd` 为空但返回成功只允许页面明确展示空 JD 且当前 fixture 能证明；否则返回 `source_invalid_output` 或 `source_not_found`。

## fetch_details_batch

- 返回映射键为输入 `platform_job_id`，每个输入恰有一个终态 outcome。
- 单岗位失败不抛出到批次外；平台级 signal 进入熔断器。
- 使用现有有界批次、间隔、重置和 tab 池参数，不新增无限重试。
- 每个 subprocess 显式携带冻结 CDP 端口。
- 事件文件只含 `kind/status/duration_ms/safe_code/platform_job_id` 等安全字段；出现 JD、Cookie、凭据、本地路径或简历内容时 artifact 无效。
- 熔断前已完成详情和单项失败均持久化；继续时按同一平台的 `platform_job_id` 跳过，不按标题或 URL 猜测。

## 熔断器与恢复

沿用现有连续两次 source signal 打开熔断器的合同。signal 仅指登录、验证、限流、封禁等平台级阻断；普通单详情失败不计入平台熔断。熔断器打开后不启动新的 source 工作，run 进入 `paused`。用户解除阻断并请求继续后，必须先按原 `platform/cdp_port/profile_key` 完成 preflight，成功后才能复位。

## 调优调用合同

调优不得使用不带平台的特殊 source factory。`stage` 继续表示五类轮次 `list/detail/rough/fine/end_to_end`。`list`、`detail` 和 `end_to_end` 轮次必须从已签发 manifest 取得完整 `frozen_runtime`，并通过与普通任务相同的平台注册表 `source_factory(frozen_runtime)` 构造 adapter。构造前至少校验：

- experiment、workload、输入 artifact、阶段 artifact、manifest 外层记录及 JSON 中的 `platform` 一致；
- 规范城市、平台城市码、映射版本、scope digest 和 task input digest 一致；
- `browser_account`、`cdp_port`、`profile_key` 与平台注册规则一致；
- `artifact_root` 仍位于该 experiment 和 round 的受控目录。

新阶段记录的数据库外层列和 artifact JSON 必须同时保存 `platform`、五类 `stage`、`source_artifact_kind`、`scope_digest` 与 `task_input_digest`。只有 `stage=list/source_artifact_kind=list` 和 `stage=detail/source_artifact_kind=detail` 可作为 source artifact；`end_to_end/rough/fine` 的 `source_artifact_kind` 均为 NULL。

`rough` 和 `fine` 轮次不创建或调用 JobSource。rough 只读取 `ready` 的 list source artifact，fine 只读取 `ready` 的 detail source artifact，并校验数据库外层列、artifact JSON、同一平台、输入版本、workload、内容摘要和筛选 schema。存量 artifact 只按 data model 的迁移前 BOSS 证明算法解释；任何错配必须在 source 或 AI 调用前返回 `tuning_platform_mismatch` 或 `manifest_validation_failed`，不得读取 UI 当前平台、全局活动账号或默认 BOSS factory 补全。

## 智联真实页面基线

2026-08-03 登录态侦察的 fixture 起点：

- 列表卡片 `div.joblist-box__item`
- 标题 `a.jobinfo__name`
- 薪资 `p.jobinfo__salary`
- 地点/经验/学历 `div.jobinfo__other-info-item`
- 公司 `a.companyinfo__name`
- 分页 `a.soupager__btn`
- JD `div.describtion-card__detail-content`

这些是可失效的外部事实，不是永久合同。实现切片开始时必须使用用户当前登录态重新核验并生成最小脱敏 fixture，同时核验明确空状态、登录墙、EdgeOne/验证码、限流和封禁 DOM。任何关键事实不匹配都要保持智联执行禁用并更新 fixture/adapter，不得以空列表、默认值或猜测编码推进。

## 必需测试替身

`FakeJobSource` 必须增加 `platform` 和 `preflight()`，并覆盖：

- BOSS/智联 adapter 可由相同编排调用；
- input hash 包含平台、规范城市、平台城市码和映射版本；
- 列表输入出现 AI 筛选时被拒绝；
- 平台或 profile 错配被拒绝；
- 带证据的真实空结果与登录墙、结构异常可区分；
- list outcome 在进度或状态推进前写入追加式 source attempt；刷新和重启后仍能按 combo 恢复 empty/failed/paused 分类；
- 每个错误矩阵分支映射到唯一状态；
- 单详情失败继续、平台级熔断暂停；
- BOSS/智联相同裸 `platform_job_id` 不发生跨平台覆盖。
- 调优 source 轮次使用 manifest 冻结 runtime 构造正确平台 adapter；rough/fine 分别只接受 list/detail source artifact，AI-only 轮次不会调用 source。
