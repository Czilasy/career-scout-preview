# Quickstart: 跨平台岗位去重（019）端到端验证

**前置**: `uv` 环境可用；`webui` 前端依赖已安装（`npm install --prefix webui`）；无需真实浏览器登录态（验证走单测与构造数据）。

## 1. 聚焦测试（最快证明）

```powershell
uv run python -m unittest tests.test_job_fingerprint tests.test_cross_platform_dedupe -v
```

预期全绿。关键用例对应 spec 验收：

- 等价写法合并（公司前后缀/标题大小写空格/城市市级）与不误合反例（标题后缀差异、城市不同、同平台）。
- 岗位只在对端**历史轮**（非最新轮）时同样剔除且追溯指向最近包含轮；超 30 天的轮不参与。
- 先落 BOSS 轮 → 智联筛选输入含等价岗 → 断言：粗筛 AI 调用清单不含该岗、判定行 dropped 且 reason 含「跨平台重复」、dropped 行 extra 携带对端身份。
- 可见性对账：进度报数（N 中 M 条重复）、完成文案三数互洽（抓取 − 重复 = 实际筛选）、任务事件含 `cross_platform_dedup` 台账。
- 开关：提交 `cross_platform_dedupe: false` 时零剔除、行为与现状一致；续跑沿用提交时取值。
- 续跑重放：中途暂停续跑后剔除不复活、计数自洽、终态正常。
- 画像过滤：对端轮摘要不一致 → 该轮不剔除；任一侧为空 → 正常参与。

## 2. 后端全量回归

```powershell
uv run python -m unittest discover tests
```

预期全绿（单平台流程行为不变的客观证据）。

## 3. 前端验证

```powershell
npm run test --prefix webui   # 若配置了测试脚本
npm run build --prefix webui  # 类型检查 + 构建
```

## 4. 端到端手验（可选，需真实登录态）

1. 同一画像、同一批关键词城市，先跑 BOSS 一键筛选至完成；
2. 再跑智联一键筛选（确保列表里含与 BOSS 结果重复的岗位）；
3. 检查点：
   - 智联任务日志/进度显示粗筛输入不含重复岗（粗筛总数 = 智联抓取数 − 跨平台重复数）；
   - 智联结果「剔除」列表出现「跨平台重复：已在 BOSS 保留」条目；
   - 两平台合并视图中该岗位只出现一条，且带「双平台在招」标注；
   - 中途暂停智联筛选再继续，最终结果与一次跑完一致；
   - `uv run python scripts/db_info.py` 确认查询库 env 后，抽查对端 BOSS 轮数据未被改动（判定/计数/时间不变）。

## 4b. 数据库抽查（可选）

对智联轮剔除行：

```sql
SELECT platform_job_id, verdict, verdict_reason, extra_json
FROM screening_results WHERE run_id = '<智联轮run_id>' AND is_dropped = 1
  AND extra_json LIKE '%cross_platform_dup_of%';
```

预期 `extra_json` 含 `cross_platform_dup_of`，指向 BOSS 轮保留条目。
