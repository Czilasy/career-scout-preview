# Quickstart: P1 灵动岛分析态与常用搜索配置包

## 前置门禁

1. 当前分支不是 `main`。
2. 所有改动只落在本 SPEC 的允许文件中。

## 灵动岛分析态

- 从长文案状态进入简历“分析中”：胶囊立即按三个字重新测量，不保留半条空白。
- “分析中”状态点颜色可见，并与抓取、JD、精筛状态可区分。
- 分析结束后切换状态：宽度继续正常更新。
- 减少动态模式下状态点仍可见。

## 聚焦验证场景

### 保存与无自动保存

- 在第二页填写关键词、城市和画像后，不点击保存便开始新一轮：列表不新增。
- 点击保存：出现一个居中的小型命名弹窗；确认后出现一个新包并收到“已保存常用配置”提示。
- 修改后再次点击保存：新增并列包，原包不变。

### 第一页复用

- 在第一页点击“使用已有配置”，列表显示全部包。
- 选择有效包后直接进入第二页，关键词、城市、画像摘要与画像事实一致。
- 选择成功后收到“已使用常用配置”提示。
- 过程不出现简历上传/AI 分析请求，不自动开始搜索。
- 切换 BOSS/智联后仍使用同一包，不产生平台副本。
- 第三页筛选状态不从包中恢复。

### 管理与失败

- 在选择框内重命名，只改变名称。
- 删除前取消确认，不发删除；确认后仅目标包消失。
- 对损坏或版本不支持的包执行选择：留在第一页、第二页数据完全不变、灵动岛显示红色错误。

## 聚焦命令

后端：

```powershell
uv run python -m unittest tests.test_search_packages tests.webui_store.test_store_migrations
```

前端（在 `webui/`）：

```powershell
npm test -- --run src/components/__tests__/DynamicIsland.spec.ts src/composables/__tests__/useSearchPackages.spec.ts src/components/__tests__/SavedSearchPackagePicker.spec.ts src/components/__tests__/SavedSearchPackageSaveActions.spec.ts src/views/__tests__/DiscoverySearchPackages.spec.ts
```

## 最终一次性门禁

本 SPEC 两个 P1 全部收敛后执行：

```powershell
uv run python -m unittest discover -s tests
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

并在 `webui/` 执行：

```powershell
npm test
npm run build
```

记录每项的测试等级与输出；自动化冒烟不得写成真实浏览器、真实账号 E2E。
