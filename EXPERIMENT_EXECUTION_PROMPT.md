# BOSS直聘抓取流程性能优化——对照实验执行提示词

> **用途**：把这份文档完整交给执行 AI，让它基于此独立完成 4 组对照实验。
> **自包含**：执行 AI 不需要访问对话历史，本文档包含所有必要上下文。
> **完整设计依据**：见 `D:\项目\boss\PERFORMANCE_EXPERIMENT_PLAN.md`

---

## 一、项目背景（自包含）

- **项目**：BOSS直聘职位抓取与筛选工具（本地单用户桌面应用）
- **技术栈**：Python + Flask + Vue + SQLite + CDP 浏览器自动化
- **代码位置**：`D:\项目\boss\webui\`
- **运行数据位置**：`~/.career-scout\`
- **高级设置文件**：`~/.career-scout\webui\advanced_settings.json`
- **入口模块**：`D:\项目\boss\webui\pipeline_exec.py`
- **当前生产耗时**：东莞 8 关键词×10 页 = 4 小时 24 分钟，目标压缩到 2 小时内

## 二、AI 端点配置（已确认）

- **端点**：`https://opencode.ai/zen/v1/chat/completions`
- **模型**：`deepseek-v4-flash`（付费版）
- **单价**：输入 $0.14 / 1M tokens，输出 $0.28 / 1M tokens
- **官方限流**：未明确 RPM/TPM 上限
- **Clash 节点切换**：**已启用**（429 触发会自动切节点，观测到的 429 次数可能低估真实限流频率）
- **opencode.ai 余额**：充足

## 三、实验目标

验证以下两类参数优化是否安全，并固化新默认值：
1. **抓 JD 三件套**：`detail_batch_size` 6→15、`detail_interval` 4→2、`detail_batch_cooldown` 15→5
2. **AI 精筛批量**：`match_batch_size` 4→8

**固定常量**（非实验变量，全组一致）：
- `match_concurrency = 6`（用户指定）
- `screen_concurrency = 3`（样本量不足以测并发，4 组全保持 3，作为 Stage A 恒定量）

## 四、共同样本

- **关键词**：「AI应用开发」1 个
- **城市**：东莞 1 个
- **列表页**：`pages=3`（约 90 条岗位）
- **执行模式**：串行，禁止并行

## 五、4 组对照配置

| 组号 | 名称 | detail_batch_size | detail_interval | detail_batch_cooldown | screen_concurrency | screen_batch_size | match_batch_size | match_concurrency |
|---|---|---|---|---|---|---|---|---|
| **1** | 基线组 | 6 | 4 | 15 | 3 | 50 | 4 | **6** |
| **2** | 抓JD组 | 15 | 2 | 5 | 3 | 50 | 4 | **6** |
| **3** | AI精筛组 | 6 | 4 | 15 | 3 | 50 | 8 | **6** |
| **4** | 全极限组 | 15 | 2 | 5 | 3 | 50 | 8 | **6** |

**变量结构**：
- 组 2：复合变量（detail 三件套）
- 组 3：**单一变量**（只改 match_batch_size）
- 组 4：组 2 + 组 3 的叠加
- `screen_concurrency` 和 `screen_batch_size` 在 4 组间完全一致 → **Stage A 是恒定量**

## 六、执行步骤

### 步骤 1：备份
```
复制 ~/.career-scout\webui\advanced_settings.json
到    ~/.career-scout\webui\advanced_settings.json.bak.experiment
```

### 步骤 2：确认 webui.db 中无进行中的 screening_run
查询 `screening_runs` 表，确认无 `status='running'` 的记录。如有，标记为 `interrupted`。

### 步骤 3：按组号顺序执行（组1→组2→组3→组4，串行）

每组执行流程：

1. **写入配置**：把该组的 7 个字段值写入 `advanced_settings.json`（注意 `pages=3`、`match_concurrency=6`、`screen_concurrency=3` 全组固定）
2. **调用 pipeline 入口**：
   - 关键词 = "AI应用开发"
   - 城市 = "东莞"
   - pages = 3
   - 不通过前端 UI，直接调用 `pipeline_exec` 入口函数（避免 UI 干扰）
3. **全程记录指标**（见第七节）
4. **该组完成后**：把结果存到 `experiment_results/group_{N}.json`

### 步骤 4：4 组全部跑完后
1. 恢复 `advanced_settings.json`（从步骤 1 的备份）
2. 生成对比表格（Markdown 格式），输出到 `experiment_results/comparison.md`
3. 按决策矩阵（第九节）得出结论
4. 在 `experiment_results/conclusion.md` 写明：哪些参数固化、哪些回退、哪些需再测

## 七、必须记录的指标（每组）

把以下指标全部记录到 `experiment_results/group_{N}.json`：

### 7.1 性能指标
- `total_duration_sec`：总耗时（秒）
- `listpage_duration_sec`：列表页抓取耗时
- `fetch_jd_duration_sec`：抓 JD 阶段耗时
- `stage_a_duration_sec`：Stage A 粗筛耗时
- `stage_b_duration_sec`：Stage B 精筛耗时
- `jd_per_job_sec`：单岗位 JD 抓取平均耗时
- `ai_per_batch_sec`：单批 AI 调用平均耗时

### 7.2 稳定性指标
- `count_429`：AI 端点返回 429 的次数
- `count_code37`：BOSS 返回反爬错误码 37 的次数
- `count_node_switch`：clash-node-switcher 触发切换的次数
- `jd_failed_count`：抓 JD 失败的岗位数

### 7.3 质量指标
- `match_count`：AI 判定匹配的岗位数
- `mismatch_count`：AI 判定不匹配的岗位数
- `uncertain_count`：AI 拿不准的岗位数
- `dropped_count`：Stage A 粗筛丢弃的岗位数

### 7.4 成本指标
- `total_input_tokens`：所有 AI 调用输入 token 总和
- `total_output_tokens`：所有 AI 调用输出 token 总和
- `total_cost_usd`：总成本（按 $0.14 input + $0.28 output per 1M tokens）
- `cost_per_job_usd`：单岗位成本

### 7.5 环境干扰判定
- `stage_a_duration_sec` 必须 4 组间差异 ≤10%。若 >10%，视为环境干扰，结论作废重测。

## 八、量化阈值

### 8.1 稳定性判定
| 指标 | "无问题" | "边界"（视为噪声）| "有问题" |
|---|---|---|---|
| 429 次数 | 0 | 1 | ≥2 |
| code:37 次数 | ≤1 | 2 | ≥3 |
| 节点切换次数 | 0 | 1 | ≥2 |
| JD 失败数 | ≤2 | 3-5 | ≥6 |

**注意**：Clash 已启用，429=0 不等于"端点无限流"，只等于"节点切换兜底成功"。但仍可据此判断"当前配置在节点切换兜底下是否可用"。

### 8.2 质量判定（与基线组对比）
| 指标 | "无退化" | "边界" | "退化" |
|---|---|---|---|
| match 分布差异 | ≤4 条 | 5-7 条且变化率 <25% | ≥5 条**且**变化率 ≥25%，或 ≥8 条任意变化率 |
| mismatch 分布差异 | ≤5 条 | 6-10 条 | ≥11 条 |

## 九、决策矩阵

| 组 2（抓JD）结果 | 组 3（AI精筛）结果 | 组 4（全极限）结果 | 决策 |
|---|---|---|---|
| 无反爬 | 无 429 且无退化 | 无问题 | **固化组 4 全部参数为新默认值**（detail 三件套 + match_batch_size=8）|
| 无反爬 | 有 429 或退化 | 有 429 或退化 | **只固化组 2 抓 JD 参数**，match_batch_size 保持 4 |
| 有反爬 | 无 429 且无退化 | 有反爬 | **只固化组 3 的 match_batch_size=8**，抓 JD 保持当前 |
| 有反爬 | 有 429 或退化 | 有问题 | **两端都保守**，回退当前值，转向策略优化 |
| 无反爬 | 无 429 且无退化 | 有问题 | **固化组 2 + 组 3 各自参数**，但不组合使用（两维度有交互）|

## 十、注意事项

### 10.1 串行执行
- 严格串行，禁止并行
- 理由：BOSS 反爬配额按 IP 计算，AI 限流是账号级，Clash 节点切换是进程级——并行会组间互相污染，无法归因

### 10.2 执行顺序固定
- 组 1 → 组 2 → 组 3 → 组 4
- 组 1 必须最先（建立基线）
- 组 4 必须最后（避免触发反爬后影响后续组）

### 10.3 中止条件
- 组 2/4 观察 `code:37`，**≥3 次立即终止该组**，记录已收集的数据后跳到下一组
- 组 3/4 观察 `429`，**≥2 次记录但不终止**（有节点切换兜底，继续跑完看最终结果）

### 10.4 环境干扰判定
- Stage A 是恒定量，4 组配置完全一致
- 若 Stage A 耗时组间差异 >10%，视为环境干扰（网络抖动、CPU 抢占等）
- 此时所有结论作废，需重测

### 10.5 配置恢复（必须）
- 实验完成或异常中止后，**必须**恢复 `advanced_settings.json` 到备份状态
- 避免影响用户后续生产使用

### 10.6 已知风险声明
- 样本量约 90 条岗位（pages=3），只验证"参数调整不退化"，不验证"极限边界"
- `screen_concurrency` 因样本不足（需 500+ 条才能测）本次不测，留待大样本实验
- 90 条的 match 数约 20-40 条，AI 随机性可能造成 ±5 条波动，质量阈值已加"变化率 ≥25%"过滤

## 十一、输出文件结构

```
experiment_results/
├── group_1.json     # 基线组原始数据
├── group_2.json     # 抓JD组原始数据
├── group_3.json     # AI精筛组原始数据
├── group_4.json     # 全极限组原始数据
├── comparison.md    # 4 组对比表格
└── conclusion.md    # 决策结论
```

### comparison.md 格式要求

```markdown
# 4 组对照实验结果对比

## 性能指标
| 指标 | 组1基线 | 组2抓JD | 组3AI精筛 | 组4全极限 |
|---|---|---|---|---|
| 总耗时（秒）| ... | ... | ... | ... |
| 抓 JD 耗时 | ... | ... | ... | ... |
| Stage A 耗时 | ... | ... | ... | ... |
| Stage B 耗时 | ... | ... | ... | ... |

## 稳定性指标
（同上格式）

## 质量指标
（同上格式）

## 成本指标
（同上格式）

## Stage A 恒定量校验
- 组1 Stage A: XX 秒
- 组2 Stage A: XX 秒
- 组3 Stage A: XX 秒
- 组4 Stage A: XX 秒
- 最大差异: XX% （应 ≤10%，否则结论作废）
```

### conclusion.md 格式要求

```markdown
# 实验结论

## 决策矩阵命中
- 组 2 结果：[无反爬/有反爬]
- 组 3 结果：[无 429 且无退化/有 429 或退化]
- 组 4 结果：[无问题/有问题]
- 命中决策：[填写决策矩阵的具体行]

## 参数固化建议
- detail_batch_size: [6/15]
- detail_interval: [4/2]
- detail_batch_cooldown: [15/5]
- match_batch_size: [4/8]
- 理由：[基于哪条决策]

## 未解决问题
- screen_concurrency: 需大样本实验单独测
- 极限边界: 需极限测试单独找

## 后续建议
[基于本次结论的下一步]
```

---

## 十二、给执行 AI 的最终指令

```
你是一个执行对照实验的 AI。你的任务是按照本文档的规范，串行执行 4 组对照实验，记录全部指标，并按决策矩阵得出结论。

执行原则：
1. 严格按文档执行，不自行修改配置或样本量
2. 串行执行，禁止并行
3. 每组完成后立即把数据写入 experiment_results/group_{N}.json
4. 任何异常都记录，不要静默吞掉
5. 实验完成或异常中止后，必须恢复 advanced_settings.json
6. 不确定的地方先停下来问，不要擅自决定

开始执行：
1. 先备份 advanced_settings.json
2. 确认无 running 状态的 screening_run
3. 按组 1 → 2 → 3 → 4 顺序执行
4. 每组完成后写入 group_{N}.json
5. 4 组完成后生成 comparison.md 和 conclusion.md
6. 恢复 advanced_settings.json
7. 向用户汇报结果

如果遇到本文档未覆盖的情况，停下来向用户确认，不要自行扩展实验设计。
```

---

**提示词结束。把本文档完整复制给执行 AI 即可开始实验。**
