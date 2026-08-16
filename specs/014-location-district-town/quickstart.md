# Quickstart: 搜索地点支持区/镇

**Created**: 2026-08-16
**Feature**: [spec.md](spec.md)

## 前置条件

- 本地后端可启动：`uv run python webui/app.py`。
- 前端依赖已安装：`cd webui && npm install`。
- 测试使用隔离数据，不触碰正式 `~/.career-scout/webui/webui.db`。
- 真实浏览器冒烟需要本地 BOSS/智联专用 Chrome 已登录。

## 静态码表刷新

```bash
uv run python -m webui.location_catalog --refresh-data
```

预期：

- `data/zhilian_districts.json` 全量生成（一次 `base/data` 请求）；
- `data/boss_business_districts.json` 生成热门城市快照，其余城市运行时逐城市兜底；
- 文件存在、非空、单文件 ≤5MB。

## 后端聚焦验证

```bash
uv run python -m unittest tests.test_location_catalog
uv run python -m unittest tests.test_location_scope
uv run python -m unittest tests.test_execution_config
uv run python -m unittest tests.test_webui_app
```

预期结果：

- 地点目录解析、城市/区从属校验、静态 + 兜底加载通过；
- 多区组合计数、`combo_key` 唯一、scope 旧摘要兼容通过；
- BOSS 隐藏参数、`_combo_hash` 含 `source_filters`、智联区县码/路由城市码映射通过；
- 预览/执行接口接受 `locations` 并写入 `script_params`；
- `/api/location/validate` 支持 `scope_kind`。

## 前端聚焦验证

```bash
cd webui
npm test -- location.spec.ts
npm test -- LocationPicker.spec.ts
npm test -- useLocationDraft.spec.ts
npm test -- JobWorkspace.spec.ts
npm test -- ResultHistoryDrawer.spec.ts
npm test -- DiscoveryView.spec.ts
```

预期结果：小方块展开、级联选择、多区、清空地点、无区数据、平台切换、旧草稿恢复、结果页/历史详情完整地点展示全部通过。

## 构建与卫生

```bash
cd webui && npm run build
uv run python -m unittest tests.test_repo_hygiene
git diff --check
```

## 手动验证场景

### 场景 1：BOSS 区/商圈选择

1. 在工作台 BOSS 平台添加“上海”。
2. 点击城市小方块，选择“浦东新区”，再选择“张江”。
3. 小方块显示“上海 · 浦东新区 · 张江”。
4. 确认范围预览：组合数/页数按地点计算。
5. 启动搜索，确认请求携带 `multiBusinessDistrict`。

### 场景 2：智联区/县选择

1. 切换到智联平台，添加“北京”。
2. 点击城市小方块，选择“朝阳区”。
3. 小方块显示“北京 · 朝阳区”，不出现商圈/镇层级。
4. 启动搜索，确认 `S_SOU_WORK_CITY` 使用区县码且空态检测仍用真实城市码。

### 场景 3：多区独立组合

1. 在上海下选择“浦东新区、徐汇区”。
2. 预览显示两个组合；进度事件、断点、输出路径不互相覆盖。
3. 暂停后续抓，确认已完成的区不重跑。

### 场景 4：旧任务兼容

1. 打开一个只有“上海”的旧草稿或历史任务。
2. 确认仍按城市级展示和执行，scope digest 恢复不报错。

### 场景 5：结果页与历史详情完整地点

1. 完成带“上海 · 浦东新区”的搜索并进入结果页。
2. 确认结果页上下文显示“上海 · 浦东新区”。
3. 打开历史抽屉进入该轮详情，确认同样显示完整地点；旧轮次只显示城市。

## 未覆盖/需真实环境验证

- 真实浏览器冒烟需要登录态；自动化只覆盖接口/组件层。
- 平台风控、验证码不影响本轮自动化判定，真实冒烟时以实际返回为准。
