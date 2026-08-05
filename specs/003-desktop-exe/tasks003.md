# Task 003：环境检查前端适配

**所属 Wave**：1（并行） | **用户故事**：EXE2（环境就绪检查与引导）、EXE5（源码模式零回归）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/contracts/runtime-mode.md`（冻结合同）
- `webui/src/components/EnvCheckDialog.vue` 与其既有测试（`webui/src/components/__tests__/EnvCheckDialog.spec.ts`）

## 写入范围（互斥）

`webui/src/components/EnvCheckDialog.vue`、`webui/src/components/__tests__/EnvCheckDialog.spec.ts`。**禁止**修改 `webui/src/api.ts`、`webui/src/types.ts`、`styles.css`、后端任何文件。

## 原子清单

- [ ] T017 [P] 记录组件现状：响应结构（CheckGroup/CheckItem）、`fixAction` 策略、既有测试覆盖点（只读）
- [ ] T018 添加**先失败**测试：`runtime_mode="exe"` 响应下，local 组 `deps` 项渲染「内置运行时」差异文案（名称/状态 ok/detail）；`webview2` 项按通用 CheckItem 渲染（✅/❌ 与 fix 按钮逻辑遵循现有 `fixAction` 策略，不引入新修复动作）
- [ ] T019 添加测试：`runtime_mode="source"`（或缺失）响应渲染与现状一致；既有测试零回归
- [ ] T020 实现 EnvCheckDialog.vue：读取 `runtime_mode`（响应缺失时默认 `"source"`），渲染差异文案与 `webview2` 项；不改变检查流程与动画
- [ ] T021 运行 `npm test -- src/components/__tests__/EnvCheckDialog.spec.ts` 与 `npm run build`，提交：仅 EnvCheckDialog.vue 及其测试，信息 `feat: adapt env check for exe mode`

## 完成定义

前端聚焦测试全绿 + build 通过；源码模式渲染路径无行为变化；不引入新 API 调用。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。