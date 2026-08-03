# Tasks 004：智联 JobSource Adapter

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks004.md。先读取仓库根目录 AGENTS.md、智联功能目录全部冻结工件、tasks001.md 和 tasks003.md；正式实施前输出【已查阅】。

本会话只负责 tasks004.md。先现场核验统一 JobSource 协议以及 tasks003 的 schema、城市、URL、runtime、脱敏 fixture、测试和提交。外部事实不完整时只能实现有证据覆盖的 adapter 部分，并保持智联新任务禁用；禁止使用历史猜测、默认值或零卡片伪装空结果。JobSource 只负责平台访问和字段归一化，不写数据库、不推进 run、不执行 AI。逐项执行节点门禁、实现和测试；不得绕过验证码或 EdgeOne，不得记录敏感页面内容。完成后只提交本任务改动，不 push，不自动执行 tasks005 或 tasks007。
```

## 给独立执行 AI 的指令

本任务只负责平台访问和字段归一化。开始时读取根 `AGENTS.md`、冻结工件、`tasks001.md`、`tasks003.md`。JobSource 不写数据库、不推进 run、不执行 AI。

## 总前置门禁

现场核验：统一 JobSource 协议存在且 BOSS/Fake 测试通过；智联 schema、城市、URL、runtime 和脱敏 fixture 存在；外部事实门禁记录明确。若 `tasks003.md` 留有未核验事实，只能实现有证据覆盖的分支，并保持平台禁用。

## 允许与禁止范围

允许修改：`scripts/zhilian_cdp_raw.py`、`webui/source.py` 中智联 adapter/factory、`tests/fixtures/zhilian/`、`tests/test_source.py` 和直接的 scraper 测试。

禁止修改：store、migration、run 状态、结果快照、收藏反馈、Vue、调优 runner。禁止自动处理验证码或 EdgeOne，禁止把零卡片直接判成真实空结果。

## 节点门禁 A：构造与 preflight

第一次创建智联 source 前，确认注册表能返回显式 `platform/browser_account/cdp_port/profile_key`，且城市映射和 enabled 状态可校验。缺失时返回稳定错误，不得回退 BOSS factory、活动账号或默认端口。

- [ ] T301 先为智联构造参数、禁用平台、缺失城市、9223 不可用和 profile 冲突编写失败测试 `tests/test_source.py`
- [ ] T302 实现 `ZhilianCdpSource` 构造及安全运行配置校验 `webui/source.py`
- [ ] T303 实现登录有效、登录墙、EdgeOne/验证码、限流、封禁、连接失败和超时的 preflight 分类 `scripts/zhilian_cdp_raw.py`
- [ ] T304 确保日志只含平台、阶段、计数、ID 是否存在和 URL host，不含 Cookie、JD、页面正文或 profile 路径

## 节点门禁 B：列表抓取

必须确认目标城市有当前映射，列表与明确空状态 fixture 版本可解释。缺任一证据时停止列表实现或返回 `platform_schema_unavailable/city_mapping_missing/source_invalid_output`，禁止伪空。

- [ ] T305 为非空列表、真实空结果、缺字段、分页、重复岗位和全局结构失效编写 fixture 测试
- [ ] T306 实现只接收关键词、规范城市解析快照和页数的 `fetch_list`，拒绝任何 AI filters
- [ ] T307 归一化 platform_job_id、标题、公司、薪资、地点、经验、学历、source/canonical URL 和 extra，缺失字段保持空
- [ ] T308 仅在明确空状态 marker 匹配当前 fixture 时返回 `empty_result=true` 和脱敏 empty_evidence，零卡片或选择器失效返回失败
- [ ] T309 生成覆盖 platform、关键词、完整城市解析快照和页数的 input_hash，并提供同 run 内稳定去重键

## 节点门禁 C：详情与批详情

首次请求详情前必须通过平台注册 URL host/path 校验，且岗位必须有可归属的 platform_job_id。不能只凭客户端任意 URL 抓取。

- [ ] T310 为详情成功、下架、超时、解析异常、登录阻断和非官方 URL 编写测试
- [ ] T311 实现 `fetch_detail` 并取得真实 JD；无法取得时返回明确单项失败，不伪造正文
- [ ] T312 实现 `fetch_details_batch`，单项异常继续，连续平台级 signal 触发熔断并返回可暂停 outcome
- [ ] T313 验证 adapter outcome 可无损表达 source attempt 所需 non_empty/empty/failed/paused、计数、证据和安全错误

## 完成门禁

先运行 fixture 全量；只有平台已启用且人工登录态可用时，再执行受控真实冒烟：一个关键词、一个已确认城市、一页列表和至少一个详情。真实冒烟只证明 adapter，不宣称完整主链完成。

```powershell
uv run python -m unittest tests.test_source tests.test_chrome_setup tests.test_repo_hygiene
```

检查无敏感 fixture、完整页面、Cookie 或路径后提交。外部事实未齐时必须在提交说明中保留禁用状态和未验证边界。

## 解锁条件

fixture 测试和 adapter 合同通过后解锁 `tasks005.md` 的真实 source 接线及 `tasks007.md` 的 list/detail/end-to-end source round。真实启用还要求本任务的受控真实冒烟通过。
