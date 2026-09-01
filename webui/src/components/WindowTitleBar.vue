<script setup lang="ts">
// ---------------------------------------------------------------------------
// 036 B084 自绘标题栏（仅桌面 EXE 渲染）：窗口顶部与主题融合的标题栏。
//
// 渲染条件：window.pywebview 存在（浏览器模式不显示，spec FR-006）。
// 布局：左侧 Career Scout 文字 + 右侧最小化 / 最大化-还原 / 关闭三按钮。
// 拖拽：标题栏空白区元素加 pywebview-drag-region 类（pywebview 6.x easy_drag
//       机制）；按钮不在该类内，点击正常（契约 §4）。
// 双击：标题栏容器显式绑定 dblclick → window_toggle_maximize（easy_drag 只
//       处理拖拽，不处理双击）。
// 主题：浅色=白、暗色=暗、特殊主题（kaleido）=透明；特殊主题下按钮半透明
//       磨砂底、X 悬停红底、最小化/最大化悬停线条变深（spec FR-008/FR-009）。
// ---------------------------------------------------------------------------
import { Minus, Square, X } from "@lucide/vue";

const isDesktop = typeof window !== "undefined" && Boolean(window.pywebview);

async function callWindow(fn: "window_minimize" | "window_toggle_maximize" | "window_close") {
  const api = window.pywebview?.api;
  if (!api?.[fn]) return;
  try {
    await api[fn]();
  } catch {
    // js_api 调用失败静默：桌面壳不可用时按钮不响应，不影响页面
  }
}

function onTitleBarDblclick(event: MouseEvent) {
  // 双击按钮不应触发最大化；只响应标题栏空白区
  if ((event.target as HTMLElement)?.closest("button")) return;
  void callWindow("window_toggle_maximize");
}
</script>

<template>
  <div v-if="isDesktop" class="window-titlebar" data-testid="window-titlebar" @dblclick="onTitleBarDblclick">
    <span class="titlebar-drag pywebview-drag-region" data-testid="titlebar-drag-region">
      <span class="titlebar-name">Career Scout</span>
    </span>
    <span class="titlebar-controls">
      <button type="button" class="titlebar-btn titlebar-minimize" data-testid="titlebar-minimize" aria-label="最小化" title="最小化" @click="callWindow('window_minimize')">
        <Minus :size="14" aria-hidden="true" />
      </button>
      <button type="button" class="titlebar-btn titlebar-maximize" data-testid="titlebar-maximize" aria-label="最大化或还原" title="最大化 / 还原" @click="callWindow('window_toggle_maximize')">
        <Square :size="12" aria-hidden="true" />
      </button>
      <button type="button" class="titlebar-btn titlebar-close" data-testid="titlebar-close" aria-label="关闭" title="关闭" @click="callWindow('window_close')">
        <X :size="15" aria-hidden="true" />
      </button>
    </span>
  </div>
</template>

<style scoped>
.window-titlebar {
  display: flex;
  align-items: center;
  height: 36px;
  flex: 0 0 auto;
  padding: 0 10px 0 16px;
  background: var(--titlebar-bg, var(--panel));
  border-bottom: 1px solid var(--hair);
  user-select: none;
}

.titlebar-drag {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  height: 100%;
  min-width: 0;
  cursor: default;
}

.titlebar-name {
  font-family: var(--font-display);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: .02em;
  color: var(--ink-2);
  white-space: nowrap;
}

.titlebar-controls {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
}

.titlebar-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 26px;
  min-height: 26px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-2);
  cursor: default;
}

.titlebar-btn:hover {
  background: color-mix(in srgb, var(--ink-2) 14%, transparent);
}

/* 特殊主题（万花筒）：标题栏透明露出主题背景；按钮半透明磨砂底 */
:global([data-theme="kaleido"]) .window-titlebar {
  background: transparent;
  border-bottom-color: transparent;
}

:global([data-theme="kaleido"]) .titlebar-name {
  color: rgba(255, 255, 255, 0.82);
}

:global([data-theme="kaleido"]) .titlebar-btn {
  background: rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(6px);
  color: rgba(255, 255, 255, 0.9);
}

:global([data-theme="kaleido"]) .titlebar-btn:hover {
  background: rgba(255, 255, 255, 0.2);
}

:global([data-theme="kaleido"]) .titlebar-btn.titlebar-close:hover {
  background: #e5484d;
  color: #fff;
}
</style>
