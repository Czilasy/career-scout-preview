# Research: P1 灵动岛分析态与常用搜索配置包

## 开源借鉴结论

查证日期：2026-09-21。三个候选都只作为交互和领域切分参考；没有一个项目同时满足“显式保存第二页身份、画像事实随包保存、双平台共用、第三页筛选排除”四项边界，因此本功能不复制候选代码或数据模型。

| 项目 | 已核实能力 | 与本需求的差异 | 许可证与维护状态 | 决定 |
|---|---|---|---|---|
| [CareerPulse](https://github.com/tcpsyn/CareerPulse) | 有搜索配置、完整结构化用户画像和 saved views CRUD | 搜索配置、画像与筛选视图分属不同资源，没有本项目的一体化“搜索身份包”边界 | 仓库可访问，LICENSE 为 MIT；当前页面未提供足够证据判断长期维护节奏 | 借鉴“配置资源独立于运行任务”的分层，不复制实现 |
| [DrJonoG/job_search](https://github.com/DrJonoG/job_search) | Saved Searches 支持命名、列表、载入、更新、删除；Prompt Config 支持多套 CV/个人摘要 | Saved Search 会保存较多筛选项，Prompt Config 又是另一套资源，不符合第三页排除和画像事实随包恢复 | 仓库可访问，LICENSE 为 Apache-2.0；当前页面未提供足够证据判断长期维护节奏 | 借鉴点击预设回填与 REST CRUD，不照搬字段集合 |
| [replyre/job-hunter](https://github.com/replyre/job-hunter) | 多套 YAML/数据库 profile 可切换并重定向搜索、评分和外联流程 | 面向整条自动任务的 active profile，不是用户在第一页选择、第二页复核的配置包 | 仓库可访问，页面显示 7 次提交；未找到独立 LICENSE 文件，因此不复制代码 | 只借鉴多套并列 profile 的概念 |

## Decision 1: 配置包是独立、平台无关的完整快照

- **Decision**: 新建 `search_packages` 领域和表，不挂在 BOSS、智联或某个当前 profile 行下；每个包保存可恢复第二页的完整快照。
- **Rationale**: 用户明确要求同一包跨平台共用，并可在第一页看到全部包。独立身份和快照能避免切画像、删画像或切平台时丢失复用能力。
- **Alternatives considered**:
  - 复用 `candidate_profiles.page2_draft`：该字段按画像隔离且会自动防抖保存，不符合“仅点击保存”和“全部包列表”。
  - 每个平台一张表/一份 payload：直接违背通用包边界。

## Decision 2: 只保存第二页通用输入与 profile_facts

- **Decision**: 快照保存推荐关键词列表、已选关键词、自定义关键词、城市文本、自定义城市、画像摘要和 `profile_facts`。不保存 `filterValues`、平台区县/商圈码、平台标识或 AI 原始响应中的第三页建议。
- **Rationale**: `profile_facts` 是无需重新分析即可恢复画像能力的结构化事实；完整 AI 原始响应混有后续筛选建议，保存它会越过 FR-003。
- **Alternatives considered**:
  - 保存整个 `resumeAnalysis`：包含不属于配置包的语义筛选数据，边界过宽。
  - 只保存画像文本：无法满足“画像背后的数据”与后续精筛所需事实。

## Decision 3: 服务端做规范化与版本校验

- **Decision**: 每个包带 `payload_version=1`；服务层在写入与读取时校验结构、类型和必要完整性，返回稳定 DTO。
- **Rationale**: 低频失效最可能来自旧版本、损坏 JSON 或缺字段。显式版本和读取校验能按用户要求拒绝，而非静默修补。
- **Alternatives considered**:
  - 客户端自行容错补默认值：会产生部分加载，违背全有或全无。
  - 不设版本：未来结构调整难以可靠识别不可用包。

## Decision 4: 名称非唯一，ID 才是身份

- **Decision**: 默认名称由首个已选关键词和城市文本组合生成，缺失时回退为“常用搜索配置”；名称可编辑、可重名，列表用 ID 区分；按 `updated_at DESC, created_at DESC` 排序。
- **Rationale**: 用户没有要求名称唯一。允许重名避免在保存时引入额外冲突规则；更新时间排序让常用包靠前。

## Decision 5: 保存统一使用新增 HTTP 语义

- **Decision**: 所有保存都使用 `POST` 创建独立新包；不提供当前包更新接口；重命名使用独立 `PATCH /{id}/name`；删除使用 `DELETE`。
- **Rationale**: API 直接表达用户动作，不用隐含 mode 字段猜测创建还是覆盖。

## Decision 6: 客户端先校验，再一次性提交状态

- **Decision**: 选择包时先获取并在局部变量中完成结构校验；成功后同步写入第二页 refs、持久化共享草稿、记录当前包 ID，最后进入第二页。任一环节失败都不切页、不写部分字段。
- **Rationale**: 满足 FR-012，并使异常测试可观察。

## Decision 7: 复用现有灵动岛错误链路

- **Decision**: 失败由 Discovery 页面发出 `{ tone: 'error', message }` notice，沿现有 App/灵动岛队列显示；不修改 `DynamicIsland.vue` 或 `App.vue`。
- **Rationale**: 现有 error tone 已承担红色错误提示，新增另一套通知系统会重复且扩大超限文件。

## Decision 8: 不自动确认画像、不自动启动搜索

- **Decision**: 配置包恢复可编辑数据，但保持现有第二页校验和确认规则；加载动作本身不调用搜索或 AI。
- **Rationale**: SPEC 明确不自动搜索，并声明既有第二页有效性要求不变。

## Decision 9: B099 只修内容身份与状态点样式

- **Decision**: 为“分析中”状态补充独立 `contentKey` 分支，使现有宽度测量 watcher 在进入和离开时触发；为该状态点增加明确静态颜色，并补自动测试。
- **Rationale**: 已定位问题是该状态落入空闲态内容键以及缺少配色规则，不需要重写灵动岛尺寸系统、轮播或动画。
- **Alternatives considered**:
  - 每帧或固定间隔重测：增加无意义测量，掩盖状态身份缺失的根因。
  - 固定胶囊宽度：会破坏其它状态按内容自适应的既有行为。
