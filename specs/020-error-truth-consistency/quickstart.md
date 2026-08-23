# Quickstart: 验证 020（错误如实呈现与数据口径一致）

**Created**: 2026-08-23 | 前置：`uv sync` 已完成；前端依赖 `cd webui && npm install` 已完成。

## 聚焦验证（按缺陷）

```powershell
# 缺陷1：熔断器透传 + 复位（tests/test_source.py）
uv run python -m unittest tests.test_source -v

# 缺陷3/6/7：续跑去重、多 run 链合并、降级恢复链（tests/test_webui_app.py 等）
uv run python -m unittest tests.test_webui_app tests.test_screen_flow tests.test_webui_store tests.test_result_rounds -v

# 缺陷4/7 store 层：画像删除、条件降级守卫（tests/test_webui_store.py）
uv run python -m unittest tests.test_webui_store -v

# 缺陷1 关联：错误码注册表不回归
uv run python -m unittest tests.test_error_registry -v
```

```powershell
# 缺陷2/5：前端（api 兜底链、运行态优先）
cd webui
npm test -- errorCodes.spec.ts useScreenRoundFlow.spec.ts screenFlow.spec.ts
```

## 全量门禁（交付前必跑，宪法 V）

```powershell
uv run python -m unittest discover -s tests
cd webui && npm test && npm run build
uv run python -m unittest tests.test_repo_hygiene
```

`npm run build` 后 `webui/dist` 有更新，随源码一并提交（pre-push 检查 dist 同步）。

## 手工冒烟（可选）

1. 断网/退出浏览器登录态后发起抓取 → 连续登录墙后提示应为「登录已失效」而非「IP 级风控拦截」，且冷却约 1 分钟后重新登录可自动恢复抓取。
2. 完成纯抓取轮 → 发起 AI 筛选 → 运行期主按钮显示「暂停筛选」；等待完成后按钮按终态显示。
3. 建画像 → 对任意岗位做收藏/反馈 → 删除画像 → 成功删除无报错。
