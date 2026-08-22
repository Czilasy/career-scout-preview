# Quickstart: 016-error-module-rework 验证指引

端到端证明本 Spec 生效的最小可跑场景。所有后端命令在仓库根目录执行。

## 前置

- `uv sync`（或既有环境）+ `webui/` 下 `npm install` 已就绪。
- 无需真实平台登录：判定链全部可用构造样本在单测中验证；真实浏览器仅手动冒烟用。

## V1 误报回归（SC-001）

```powershell
uv run python -m unittest tests.test_risk_signal_tiers -v
```

预期：正常空结果、单次 403 后复探通过、岗位正文含"滑块/验证码/429"、探测返回正常已登录数据 ——
全部断言无 `source_account_restricted`/restricted 缓存写入/冷却副作用。

## V2 实锤暂停（SC-002）

同上测试文件中 confirmed 组样本：验证码页、限流提示页、code:31、连续两次 403/429 ——
断言产生对应实锤码且进入派生阻断集合；`tests.test_error_registry -v` 通过派生集合一致性断言。

## V3 软失败留痕（SC-003）

```powershell
uv run python -m unittest tests.test_healthy_pipeline tests.test_webui_app -v
```

预期：部分组合软失败 → 任务成功收尾 + `combo_issue` 事件落库；task-state 响应含 `combo_issues`；
全组合软失败零结果 → failed + 中性文案。

## V4 冷却移除（SC-005）

```powershell
uv run python -m unittest tests.test_webui_app tests.test_env_check -v
ls webui/cooldown.py   # 应不存在
```

预期：提交不再有冷却守卫；env-check 无 `cooldowns` 字段；`/api/cooldown/clear` 404。

## V5 进度无跳变（SC-006）

```powershell
cd webui && npm test -- --run
```

预期：前端 spec 断言恢复路径首拍进度 = 断点值（列表/JD/AI 三线）。

## V6 全量门禁（交付前）

```powershell
uv run python -m unittest discover -s tests -v
cd webui && npm test -- --run && npm run build
uv run python -m unittest tests.test_repo_hygiene
```

## 手动冒烟（可选）

1. `uv run python webui/app.py` 打开工作台，跑一次真实小任务（1 关键词 1 页）。
2. 中途点暂停 → 继续三次，观察进度条始终从断点起步。
3. 环境检查面板不再出现"建议等待至 XX 点"；账号区无冷却入口。
