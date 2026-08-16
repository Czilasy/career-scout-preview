# Quickstart: AI 筛选停止/继续/恢复链路统一

**Created**: 2026-08-15
**Feature**: [spec.md](spec.md)

## 前置条件

- 本地后端可启动：`uv run python -m webui.app` 或项目既有启动入口。
- 前端依赖已安装：`cd webui && npm install`。
- 测试库使用隔离数据，不触碰正式 `~/.career-scout/webui/webui.db`。

## 后端聚焦验证

```bash
uv run python -m unittest tests.test_screen_flow
uv run python -m unittest tests.test_store_screen_resume
uv run python -m unittest tests.test_webui_app
```

预期结果：暂停路由、续跑候选扩展、round_context 透传、JD 回退、user_finished 元数据更新用例全部通过。

## 前端聚焦验证

```bash
cd webui
npm test -- screenFlow.spec.ts
npm test -- useScreenRoundFlow.spec.ts
npm test -- ScreenRoundActions.spec.ts
npm test -- DiscoveryView.spec.ts
```

预期结果：状态派生、恢复回填、按钮矩阵、暂停/继续反馈、重抓跳转用例全部通过。

## 构建与卫生

```bash
cd webui && npm run build
uv run python -m unittest tests.test_repo_hygiene
git diff --check
```

## 手动验证场景

### 场景 1：暂停后 04 可见部分结果

1. 启动一轮 AI 筛选，等待出现部分进度。
2. 点击“暂停筛选”。
3. 按钮先显示“正在暂停…”，随后 03 显示“继续 AI 筛选”“查看结果”和“结束并保存结果”。
4. 进入 04，确认已判定/待确认岗位可见。

### 场景 2：04 继续直接续跑

1. 保持暂停态。
2. 在 04 点击“继续 AI 筛选”。
3. 页面跳到 03，关键词/城市/六类条件/画像/已确认自动回显，任务自动续跑。
4. 完成后自动回到 04。

### 场景 3：重抓进度在 03

1. 进入 04 待确认区，点击“全部重抓”。
2. 需要选平台时先在 04 选择。
3. 页面自动跳到 03 显示重抓进度。
4. 完成自动回 04；暂停/失败留在 03 并显示对应按钮。

### 场景 4：失败/结束保存后仍可续跑

1. 构造 AI 筛选失败或点击“结束并保存结果”。
2. 03/04 显示“继续 AI 筛选”。
3. 点击后确认只续跑未完成岗位，已判定结果保留。

## 未覆盖/需真实环境验证

- 真实浏览器抓取与 AI 调用需要登录态和可用 AI 配置；自动化只覆盖到接口/组件层。
- 服务重启后的断点恢复依赖真实 DB 场景，需在隔离库手工验证一次。

### 场景 5：2993 回归（条件不得无条件下发）

1. 构造本轮已冻结六类条件的暂停态。
2. 模拟恢复路径返回空条件，验证前端阻断继续并提示，不发起无条件下发初筛。
3. 恢复成功后验证初筛请求携带本轮冻结条件。
