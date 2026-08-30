# Data Model: 万花筒彩蛋主题模块

**Date**: 2026-08-30

## 实体

### ThemeMode（主题模式）

用户选择的外观偏好。值域：`"light" | "dark" | "kaleido"`（由原二值扩展）。

- **存储位置**：
  - 本地：`localStorage["career-scout-theme-mode"]`（既有键，不换键）
  - 后端：主题偏好文件（经 `ctx._theme_path()`，JSON `{"mode": "<ThemeMode>"}`）
- **校验规则**：三处读取点（localStorage 回读、后端回读、PUT 入参）均必须接受三值、拒绝其他值（拒绝时回落既有行为：本地回落 light、PUT 返回 400）
- **优先级**：用户手动选择 > 后端启动回读 > 系统偏好 > 默认 light（既有规则不变，仅值域扩展）

### 主题注册项（Theme Registration）

注册表中一条登记，字段：

- **id**: `"light" | "dark" | "kaleido"`（与 ThemeMode 同值域）
- **label**: 弹层显示名（亮 / 暗 / 万花筒）
- **swatch**: 标本块视觉（亮＝白玻璃切片；暗＝黑玻璃棱线；万花筒＝流动微缩 rosette）
- **kaleido 专属**: 模块资源挂载信息（样式与光场组件经模块自身装载，注册表只登记存在与排序）

### 光场会话状态（Kaleido Motion Session，会话级）

- **entryPlayed**: 本次会话是否已播过入场转场（sessionStorage 标记，决定首启仪式只播一次）
- **calmUntil**: 逃生舱静止截止时刻（点 logo 后 3 秒）
- **awake**: 瞳孔苏醒态（光标距光核 <210px）

## 状态转移

```text
            ┌───────────── 普通点击（ripple，现状不变）─────────────┐
            │                                                      │
   light ───┤                                                      ├──→ dark
            │   长按→选万花筒（首次：转场；此后：0.2s 过渡）         │
            └──────────────────────→ kaleido ←─────────────────────┘
                                        │
                              长按→选 light / dark（0.2s 过渡）
```

- `light ↔ dark`：`toggleTheme` 现状，ripple 不变
- `* → kaleido`：mode 写入三链路＋模块挂载（首次含入场转场）
- `kaleido → light/dark`：模块卸载＋0.2s 过渡；`toggleTheme` 在 kaleido 态下普通点击落 light（既有 toggle 逻辑的自然延展，不改函数）

## 校验与不变量

- 三处持久化读取点值域一致（local/后端回读/PUT），任何一处不接受 `kaleido` 即为缺陷
- 亮/暗主题下的 DOM 结构、类名、令牌与现状完全一致（kaleido 样式全部限定在 `[data-theme="kaleido"]` 作用域与光场组件内）
- 转场进行中重复触发被忽略（幂等）
