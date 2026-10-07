# Contract: 047 错误、重试、收尾与历史

本文规定已确认目标的实现契约，技术字段不是额外用户需求；保持旧调用兼容。

## C1 通用失败事实

- SourceOutcome 仍使用 ok/failed_code/failed_reason/safe_log。平台 probe 四态保持公开入口。
- classify_preflight_failure 的载荷始终保留规范 failed_code 和安全 error；systemic 另保留 hard_stop/hard_stop_code。完整性独立 integrity，不能覆盖 failed_code。
- 预检失败给所有尚未启动计划单元记录阻断事实，保留 planned/未执行含义；不伪造页完成或空结果。实际成功仍由 033 判断。
- 安全 probe 摘要仅 HTTP 类别、结构类别、已知规范业务码、CDP 失败阶段、尝试序号；不保存正文、URL查询凭据、Cookie、Token 或个人信息。
- registry/source_* 码沿现有定义；未知保持 source_status_unclear，浏览器不可用保持 source_cdp_unavailable。登录复核绕过旧缓存。
- 有具体失败原因的partial经store_run_lifecycle保留错误字段；旧store_runs的partial更新会清空错误，不能调用该旧方法后宣称原错误已保存。

## C2 failed 单轨 retry

`POST /api/flows/{flow_id}/tracks/{platform}/retry`

请求：profile_id、expected_track_id、expected_run_id、expected_updated_at。expected_run_id 为当前阶段拥有的 screen_run_id 或 scrape_run_id；预检无 run 用空串，不等于不校验；updated_at 用已读 Track 值。

成功保持旧响应形状 `{ok:true, flow:...}`。Flow/Track id 不变，目标绑定新 run；兄弟全部不变。平台参数只作注册身份，不让通用层写平台特例。

| 条件 | 结果 |
|---|---|
| 精确画像/轨道，当前 failed 版本，冻结输入有效 | CAS、新 run、提交一个 worker |
| failed AI，已存抓取输入有效 | 新 AI run，复用 source scrape，原 auto/条件语义 |
| 缺 snapshot/抓取输入/身份 | 409 安全可解释原因，无提交，保留旧失败 |
| 旧版本、旧阶段/旧run、跨画像、已活动/已终结非failed | 409 冲突或现有归属拒绝，无提交 |
| 两次相同失败版本请求并发 | 最多一个成功提交；另一次409，不返回虚假成功 |
| claim 后 worker 提交失败 | 安全503/原稳定错误，目标新尝试真实收口，兄弟不变 |

旧 pause/resume/stop API 的字段和行为不变。retry 不绕普通终态门禁；不把 failed 的 resume 改成 retry。

当前版本 CAS 方法放 store_flow_retry，不放宽全局 status 转移。新 params 写 retry_of_run_id；保持失败原因、白箱、旧任务可追踪。Track 原 result 在新结果提交前保留。

同一写事务核对目标仍为该画像当前Flow（沿既有current查询口径），没有另一个Flow占用活动门禁；新轮创建与旧轮retry竞争只能一个取得运行资格。不能从历史failed轨道复活另一轮并抢走当前页面。

retry事务创建新执行记录只允许INSERT新id，禁止REPLACE旧记录。后续复用submit/快照/worker初始化时跳过重复主记录创建和已完成绑定；初始化失败仅补偿已claim新attempt。

前端 action kind 为 `retry-track`，测试 id 为 `retry-flow-track`；事件携带平台及当前身份；操作响应仍以权威 Flow 投影。无 run failed 也有有效入口，busy/stale/身份冲突不能制造假按钮。

## C3 正常收尾/当前尝试

task-state 可选扩展 closure `{kind:'finish'|'stop', phase:'pending'|'committed'}`，无事实为 null。字段从现有原因、绑定、结果与 live 请求推导，不持久化新表。

- finish pending 不等于成功；显示正在结束保存，保留已观测进度。结果提交成功且当前绑定一致才 committed。
- stop 保持现有终止语义，不隐式保存。终止正常完成不能短暂显示硬失败，也不强制跳04；结果页面按既有可达规则推进。
- 单轨 user_finished 不等待兄弟结束才解释为正常收尾；TaskProgress 使用同一事实而非全Flow roundClosed 猜测。
- 有真实失败时，错误优先、closure 不掩盖。finish失败可以按原入口重试保存，不能变成新业务 retry。
- 写状态/绑定/结果前均核对当前 run；旧run只记录 late_callback 诊断，不能改结论/新run/当前结果。
- 白箱 append/upsert/finalize 在各自既有写事务内调用 store_whitebox_lifecycle 的公开写入策略：明确 user_finished claim、已终结 owner 或旧尝试不再投影运行单元，迟到事实改为诊断；禁止先在service读完再无条件写库的检查/写入竞态。
- 同run继续时捕获原worker实例，在ctx.lock内核对当前task实例再协调写入；unit/page事实比较既有attempt_no，不将旧attempt自动升成当前attempt。run id相同本身不足以接受旧回调。

停止/保存仍调用旧路由；立即/等本批选择保留。无运行对象的排队终止保留现有事务；task/Track自然完成先提交的，不能被迟到命令改写成错误失败。

## C4 前端代次和阶段投影

- 操作 epoch 绑定 profile/flow/track/run，启动动作废弃此前的请求；GET与action响应均检查epoch，不能只给GET加守卫。
- 另一轨道继续轮询，状态按各自当前身份接受；不以一个轨道忙态冻结整个Flow。
- refresh 先在局部对象取得 Flow/两段快照；flow publication 和 UI projection 使用捕获版本，异步期间不混读 mutable live ref。
- scrapeItems、screenItems、可达页和操作事实一次同步提交，之后执行导航/结果通知。数据读取失败保持旧可信现场并显式 stale，不发布一半新一半旧。
- 终态绑定或 retry 换run使旧poll/旧result响应无效；页面已解锁集合仍单调，结果合流保留阅读现场及单次通知。

## C5 进度收拢与按钮

- 真实阶段进度首次出现触发对应抽屉收拢：02 search + advanced，03 screen。单平台/全部、手动/自动一致。
- scene身份恢复完成后再应用该阶段首次进度收拢，写既有 cardOpenStates；同一阶段轮询、兄弟到达不反复执行关闭。已有手动现场策略继续保留，不新增跨轮偏好。
- 点击启动但请求被拒绝、进度未出现，不能凭 loading 关闭配置；历史只读现场不冒充执行启动。
- 按钮通过共享组件呈现，compact 为局部密度；暂停/继续/重试、保存、终止层级明确。数字像素不作为验收门槛。
- 桌面和窄屏文案/焦点/点击可用、忙态不跳布局、无溢出遮挡；不改主题选择或明暗规则。

## C6 历史资格与删除

沿现有 GET历史与 `DELETE /api/result-history/{run_or_track_id}?profile_id=...`。不新增外层整流程删除。

聚合平台轨/平台轮次兼容扩展 can_delete:boolean、delete_block_reason:string|null；统一store helper只读算出。旧响应无字段保留兼容显示，但DELETE必须重判。

资格同时核对：目标归属、Track生命周期、全部关联执行/子任务、共享引用、retry/finish pending。不同平台/画像/Flow引用拒绝，logging不当worker。

仅允许局部修正“同 id screening execution有确定终结事实、Track终结、同轨归属、无实际active任务/子任务”的 queued search。其他矛盾不能猜终态。GET不修改；DELETE BEGIN IMMEDIATE 内修正/重判/删除一体回滚。

终态不是全部成功：failed/stopped/done都可能可删。真正queued/running/paused、证据不足的interrupted和pending继续保护。删除一轨不删兄弟；最后一轨删除才清外壳。

retry旧尝试纳入同轨闭包，但查询归属/子父边不得穿越其他轨道。用户岗位资产继续按现有范围处理。

## C7 不变验收

所有平台同一通用规则；真实错误可查；任何新平台适配不需改本通用契约；正常收尾不伪造完整成功；旧失败诊断保留；正式历史不批量修补；既有046/033只读。本轮仅文档，不声明上述契约已实现。
