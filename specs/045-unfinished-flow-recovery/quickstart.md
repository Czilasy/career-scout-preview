# Quickstart: 聚焦验证

```powershell
uv run python -m unittest tests.test_run_lifecycle
uv run python -m unittest tests.test_result_rounds
uv run python -m unittest tests.test_desktop_shell
cd webui; npm test -- --run src/composables/__tests__/useDiscoveryTasks.spec.ts src/composables/__tests__/useDiscoveryExecution.spec.ts
```

最终门禁（B101–B103 收敛后）：

```powershell
uv run python -m unittest discover -s tests
cd webui; npm test
cd webui; npm run build
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

B104 真实界面三条单独执行，不用上述自动命令替代。
