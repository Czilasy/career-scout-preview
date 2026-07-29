# Round 2 对照实验执行提示词——找 match_concurrency 天花板

> **用途**：把这份文档完整交给执行 AI，让它基于此独立完成 round2 对照实验。
> **自包含**：执行 AI 不需要访问对话历史，本文档包含所有必要上下文。
> **脚本已就绪**：`D:\项目\boss\experiment_results\round2\_orchestrator_round2.py`（已写好并验证路径正确）

---

## 一、项目背景

- **项目**：BOSS直聘职位抓取与筛选工具（本地单用户桌面应用）
- **代码位置**：`D:\项目\boss\webui\`
- **运行数据位置**：`~/.career-scout\`
- **高级设置文件**：`~/.career-scout\webui\advanced_settings.json`
- **入口模块**：`D:\项目\boss\webui\pipeline_exec.py`

## 二、Round 1 已完成（背景）

上一轮对照实验已完成，4 组配置中所有 `match_concurrency` 固定为 6，结果：
- 4 组全部 `429=0`
- 4 组全部 `code:37=0`
- → **说明 match_concurrency=6 远未触限流边界**

Round 1 已验证的安全基础：
- `detail_batch_size=15`、`detail_interval=2`、`detail_batch_cooldown=5` → code:37=0（安全）
- `match_batch_size=8` 会让 uncertain 异常增长（8 倍），**不可作为固定基础**
- `match_batch_size=4` 没有质量警告 → **本轮固定为 4**

## 三、本轮实验目标

**找 match_concurrency 的天花板**——即 429 首次开始触发的并发值。

**为什么找天花板**：
- Round 1 全部 429=0 说明没压到边界
- 用户明确要求"性能和速度"，需要找到能用的最大并发值
- 找到天花板后，固化到天花板的安全前置值（如天花板是 20，则固化到 15）

## 四、实验设计

### 4.1 唯一变量
**`match_concurrency`**（精筛并发数）

### 4.2 固定基础（4 组全一致）
```json
{
  "pages": 3,
  "inter_combo_delay": 10.0,
  "detail_reset_every": 4,
  "screen_batch_size": 50,
  "screen_concurrency": 3,
  "match_batch_size": 4,
  "detail_batch_size": 15,
  "detail_interval": 2,
  "detail_batch_cooldown": 5
}
```

### 4.3 4 组梯度
| 组号 | 名称 | match_concurrency | 目的 |
|---|---|---|---|
| C1 | concurrency_10 | 10 | 前端上限 |
| C2 | concurrency_15 | 15 | 突破前端 |
| C3 | concurrency_20 | 20 | 高并发 |
| C4 | concurrency_30 | 30 | 找天花板（样本跑不满也无所谓，看 429 是否触发）|

### 4.4 共同样本
- 关键词：「AI应用开发」
- 城市：东莞
- pages=3（约 90 条岗位）
- 串行执行，禁止并行

## 五、脚本位置与使用

### 5.1 脚本路径
```
D:\项目\boss\experiment_results\round2\_orchestrator_round2.py
```

脚本已写好并验证：
- ✅ 路径正确（PROJECT_ROOT 上溯 2 级）
- ✅ 备份逻辑正确（生成 `.bak.round2`，不覆盖 round1 的 `.bak.experiment`）
- ✅ 变量唯一性正确（只有 match_concurrency 在变）
- ✅ 4 组串行 + 60s 组间冷却
- ✅ 异常捕获 + 自动恢复配置
- ✅ 输出到 `experiment_results/round2/group_C{1-4}.json`

### 5.2 执行命令
```powershell
cd D:\项目\boss
python experiment_results\round2\_orchestrator_round2.py
```

或只跑某一组：
```powershell
python experiment_results\round2\_orchestrator_round2.py 2   # 只跑 C2
```

## 六、执行前预检（必须按顺序执行）

### 预检 1：BOSS 登录态（最关键，否则全部失败）

Round 2 首次执行时已发现登录态过期，导致 C1 在 9 秒内失败：
```
list_scrape_failed: 浏览器已打开，但还未登录 BOSS
```

**执行 AI 必须先确认登录态**：
1. 调用 `webui.pipeline_exec.ensure_chrome_ready()` 启动调试浏览器
2. 调用 `webui.source.preflight()` 检查登录状态
3. 如果返回 `source_login_required`：
   - **必须停下来让用户登录**——在浏览器中打开 https://www.zhipin.com/ 完成登录
   - 用户确认登录后，重新执行脚本
4. 如果返回 ok，继续后续预检

**禁止**：不要试图自动登录或绕过登录检查。

### 预检 2：Flask 服务

检查 5000 端口是否被占用：
```powershell
Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue
```

如果有占用（Flask 在跑），**必须先杀掉**，避免 SQLite 锁竞争：
```powershell
Stop-Process -Id <PID> -Force
```

### 预检 3：CDP 端口

```powershell
Get-NetTCPConnection -LocalPort 9222 -ErrorAction SilentlyContinue
```

如果占用，杀掉旧进程。脚本会自己启动 CDP。

### 预检 4：备份文件冲突

检查 `~/.career-scout\webui\advanced_settings.json.bak.round2` 是否已存在：
- 如果存在（上次中断留下的），删除它，让脚本重新生成干净备份
- 如果不存在，跳过

## 七、执行步骤

### 步骤 1：预检
按第六节执行全部 4 项预检。**任何一项不通过都停下来让用户处理**，不要硬跑。

### 步骤 2：启动脚本
```powershell
cd D:\项目\boss
python experiment_results\round2\_orchestrator_round2.py 2>&1
```

非阻塞执行（脚本会跑 40-60 分钟），用 CheckCommandStatus 轮询进度。

### 步骤 3：监控进度
- 每组完成后会写入 `experiment_results/round2/group_C{N}.json`
- 每写完一个 JSON，立即读取检查 `count_429` 字段
- **如果某组的 count_429 > 0，记录下来但不要终止**——继续跑完所有组，看 429 是否随并发递增

### 步骤 4：4 组全部完成后
1. 读取 4 个 group_C{1-4}.json
2. 生成对比表格 `experiment_results/round2/comparison.md`
3. 按决策矩阵（第九节）得出结论
4. 在 `experiment_results/round2/conclusion.md` 写明天花板值和建议固化值
5. **必须确认** `advanced_settings.json` 已恢复（脚本会自动恢复，但要验证）

## 八、必须记录的指标

脚本已自动记录以下指标到 group_C{N}.json，执行 AI 只需读取：

### 8.1 性能指标
- `total_duration_sec`：总耗时
- `listpage_duration_sec`：列表页抓取耗时
- `fetch_jd_duration_sec`：抓 JD 阶段耗时
- `stage_a_duration_sec`：Stage A 粗筛耗时（**恒定量，组间差异应 ≤10%**）
- `stage_b_duration_sec`：Stage B 精筛耗时（**这是 match_concurrency 变量影响的阶段**）
- `jd_per_job_sec`：单岗位 JD 抓取平均耗时
- `ai_per_batch_sec`：单批 AI 调用平均耗时

### 8.2 稳定性指标（**最关键**）
- `count_429`：AI 端点返回 429 的次数 → **找天花板的核心指标**
- `count_code37`：BOSS 反爬错误码 37 的次数（应为 0，detail 已验证安全）
- `count_node_switch`：节点切换次数（Clash 已启用，可能吸收部分 429）
- `jd_failed_count`：抓 JD 失败的岗位数

### 8.3 质量指标
- `match_count` / `mismatch_count` / `uncertain_count` / `dropped_count`

### 8.4 成本指标
- `total_input_tokens` / `total_output_tokens` / `total_cost_usd` / `cost_per_job_usd`

## 九、决策矩阵

### 9.1 天花板判定逻辑

按 C1→C2→C3→C4 顺序读取 `count_429`：

| 首次 count_429 > 0 的组 | 天花板判定 | 建议固化值 |
|---|---|---|
| C1 (10) | 天花板 ≤ 10 | **不固化**，回退到 round1 已用的 6 |
| C2 (15) | 天花板在 (10, 15] | 固化到 10 |
| C3 (20) | 天花板在 (15, 20] | 固化到 15 |
| C4 (30) | 天花板在 (20, 30] | 固化到 20 |
| 全部为 0 | 天花板 > 30 | 固化到 30，需后续更高梯度测试 |

### 9.2 质量退化判定

与 C1（最低并发，作为本轮基线）对比：
- `uncertain_count` 差异 ≥5 条且变化率 ≥25%，或 ≥8 条任意变化率 = 退化
- `match_count` 差异 ≥5 条且变化率 ≥25%，或 ≥8 条任意变化率 = 退化

**如果某组触发退化**：即使 429=0，也不能固化到该值，回退到上一组的安全值。

### 9.3 环境干扰判定

Stage A 是恒定量（4 组配置完全一致）：
- Stage A 耗时组间差异 >10% → 环境干扰，结论作废重测
- Stage A 耗时组间差异 ≤10% → 实验环境稳定，结论可信

## 十、Clash 节点切换的已知影响

Clash 已启用，429 触发会被 clash-node-switcher 主动切换节点避让。因此：
- **观测到的 count_429 可能低估真实限流频率**
- 429=0 不等于"端点无限流"，只等于"节点切换兜底成功"
- 但仍可据此判断"当前配置在节点切换兜底下是否可用"

如果某组 count_node_switch > 0，说明该并发下节点切换开始介入——这本身也是"接近天花板"的信号，**即使 count_429=0，count_node_switch > 0 也应视为天花板信号**。

修正后的天花板判定：
- **count_429 > 0** 或 **count_node_switch > 0** → 视为触天花板

## 十一、注意事项

### 11.1 串行执行
- 严格串行，禁止并行
- 脚本已内置 60s 组间冷却

### 11.2 执行顺序固定
- C1 → C2 → C3 → C4
- C1 必须最先（建立本轮基线）
- C4 必须最后（最高并发，避免影响后续组）

### 11.3 中止条件
- `code:37 ≥ 3` 立即终止该组（不应触发，detail 已验证安全）
- `count_429 ≥ 2` 记录但不终止（继续跑完看天花板趋势）
- `count_node_switch ≥ 2` 记录但不终止（同上）

### 11.4 配置恢复（必须）
- 实验完成或异常中止后，**必须**验证 `advanced_settings.json` 已恢复
- 脚本会自动从 `.bak.round2` 恢复，但要二次确认
- 验证方法：读取 advanced_settings.json，确认 `match_concurrency` 不是 10/15/20/30

### 11.5 样本量风险声明
- 90 条样本（pages=3）只验证"天花板在哪"
- 高并发组（C3/C4）可能样本跑不满（90 条 ÷ 4 = 22 批，30 并发只能并行 22 个）
- 但 429 是否触发仍是有效指标，即使跑不满

## 十二、输出文件结构

```
experiment_results/round2/
├── _orchestrator_round2.py     # 脚本（已就绪）
├── group_C1.json               # C1 (concurrency=10) 原始数据
├── group_C2.json               # C2 (concurrency=15) 原始数据
├── group_C3.json               # C3 (concurrency=20) 原始数据
├── group_C4.json               # C4 (concurrency=30) 原始数据
├── comparison.md               # 4 组对比表格
└── conclusion.md               # 天花板判定 + 固化建议
```

### comparison.md 格式

```markdown
# Round 2 对照实验结果对比

## 核心指标（找天花板用）
| 组 | match_concurrency | count_429 | count_node_switch | Stage B 耗时 |
|---|---|---|---|---|
| C1 | 10 | ... | ... | ... |
| C2 | 15 | ... | ... | ... |
| C3 | 20 | ... | ... | ... |
| C4 | 30 | ... | ... | ... |

## 性能指标
| 指标 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|
| 总耗时 | ... | ... | ... | ... |
| Stage A 耗时（恒定量）| ... | ... | ... | ... |

## 稳定性指标
（同上格式）

## 质量指标
（同上格式）

## 成本指标
（同上格式）

## Stage A 恒定量校验
- 4 组 Stage A 耗时最大差异：XX%
- 结论：环境稳定/作废重测
```

### conclusion.md 格式

```markdown
# Round 2 实验结论

## 天花板判定
- 首次 count_429 > 0 的组：CX (match_concurrency=XX)
- 首次 count_node_switch > 0 的组：CX (match_concurrency=XX)
- 综合天花板判定：match_concurrency = XX
- 建议固化值：XX（天花板的安全前置值）

## 质量退化检查
- 与 C1 对比的 uncertain/match 差异：...
- 是否触发退化阈值：是/否

## 环境干扰校验
- Stage A 恒定量组间差异：XX%
- 结论：环境稳定/作废重测

## 决策矩阵命中
- 命中行：[填写]
- 固化建议：[填写]

## 未解决问题
- [如有]

## 后续建议
- [基于本次结论的下一步]
```

---

## 十三、给执行 AI 的最终指令

```
你是一个执行对照实验的 AI。你的任务是按照本文档的规范，执行 Round 2 对照实验，找到 match_concurrency 的天花板。

执行原则：
1. 严格按文档执行，不自行修改配置或样本量
2. 必须先完成所有预检（特别是 BOSS 登录态）
3. 串行执行，禁止并行
4. 每组完成后立即读取 group_C{N}.json，检查 count_429 和 count_node_switch
5. 任何异常都记录，不要静默吞掉
6. 实验完成或异常中止后，必须验证 advanced_settings.json 已恢复
7. 不确定的地方先停下来问用户，不要擅自决定

执行流程：
1. 预检 1：确认 BOSS 登录态（如过期，停下来让用户登录）
2. 预检 2：杀掉占用 5000 端口的 Flask 进程
3. 预检 3：确认 9222 端口空闲
4. 预检 4：清理旧 .bak.round2 备份
5. 启动脚本：python experiment_results\round2\_orchestrator_round2.py
6. 轮询进度，4 组全部完成后读取 4 个 JSON
7. 生成 comparison.md 和 conclusion.md
8. 验证 advanced_settings.json 已恢复
9. 向用户汇报：天花板值、建议固化值、是否需重测

如果遇到本文档未覆盖的情况，停下来向用户确认，不要自行扩展实验设计。

特别注意：
- BOSS 登录态是 round2 首次执行时的失败点，必须先解决
- count_node_switch > 0 也算触天花板（即使 count_429=0）
- Stage A 耗时组间差异 >10% 视为环境干扰，结论作废重测
```

---

**提示词结束。把本文档完整复制给执行 AI 即可开始实验。**
