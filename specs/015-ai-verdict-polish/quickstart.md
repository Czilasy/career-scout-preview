# Quickstart: Spec 015

## 聚焦验证

```powershell
uv run python -m unittest tests.test_ai tests.test_ai_retry tests.test_ai_prompts tests.test_profile_facts tests.test_tuning
Set-Location webui
npm test
npm run build
Set-Location ..
```

## 完整验证

```powershell
uv run python -m unittest discover tests
uv run python -m unittest tests.test_repo_hygiene
git diff --check
```

## Chrome CLI 入口冒烟

```powershell
uv run python scripts/boss_cdp_raw.py --help
uv run python scripts/boss_cdp_raw.py --copy-login-state --check --cdp-port 1
uv run python scripts/boss_cdp_raw.py --import-boss-session --source-cdp-port 2 --cdp-port 1
```

这些命令只验证参数门禁和启动导入，不连接真实 Chrome；真实窗口简历分析/精筛仍需登录态和可用 AI 端点。
