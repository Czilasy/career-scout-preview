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
// 主题：浅色=白、暗色=暗；特殊主题大类（data-theme-category="special"，
//       万花筒及后续特殊主题）窗口控制条为半透明深色毛玻璃磨砂 + 强模糊，
//       X 悬停红底白字、最小化/最大化悬停变深色（spec FR-008/FR-009）。
//       磨砂样式挂在大类标记上而非具体主题 id，新特殊主题自动继承（A9）。
// 图标：最大化/还原按钮图标随窗口真实状态切换（FR-004）——最大化=单方框、
//       还原=重叠方块；初始挂载查询一次，toggle/双击后用返回值同步。
// ---------------------------------------------------------------------------
import { Copy, Minus, Square, X } from "@lucide/vue";
import { onMounted, onUnmounted, ref } from "vue";

// T021 真机修复：window.pywebview 由 pywebview 在页面导航完成后才异步注入，
// 晚于 Vue 挂载。setup 一次性判断会导致标题栏整条不渲染（真机「窗口手柄
// 没了」）。改为响应式 ref + 监听 pywebviewready 事件补渲染。
const isDesktop = ref(typeof window !== "undefined" && Boolean(window.pywebview));

// T023 补全：窗口是否最大化（驱动按钮图标切换）。窗口真实状态由桌面壳
// events.maximized/restored 维护，前端经 js_api 查询/返回值同步。
const isMaximized = ref(false);

async function syncMaximized() {
  const api = window.pywebview?.api;
  if (!api?.window_is_maximized) return;
  try {
    const res = await api.window_is_maximized();
    if (res && typeof res.maximized === "boolean") isMaximized.value = res.maximized;
  } catch {
    // 查询失败静默：图标保持默认普通态
  }
}

function handlePywebviewReady() {
  if (!isDesktop.value) isDesktop.value = true;
  void syncMaximized();
}

onMounted(() => {
  if (typeof window === "undefined") return;
  if (window.pywebview) {
    isDesktop.value = true;
    void syncMaximized();
  } else {
    window.addEventListener("pywebviewready", handlePywebviewReady);
  }
});

onUnmounted(() => {
  if (typeof window !== "undefined") {
    window.removeEventListener("pywebviewready", handlePywebviewReady);
  }
});

async function callWindow(fn: "window_minimize" | "window_close") {
  const api = window.pywebview?.api;
  if (!api?.[fn]) return;
  try {
    await api[fn]();
  } catch {
    // js_api 调用失败静默：桌面壳不可用时按钮不响应，不影响页面
  }
}

// T027 补全：点 X 后保持红底白字「正在关闭」反馈，直至窗口退出；若桌面壳
// 返回失败（ok:false）则复位按钮，避免卡在红框无法再操作。
const isClosing = ref(false);

async function onWindowClose() {
  if (isClosing.value) return;
  isClosing.value = true;
  const api = window.pywebview?.api;
  if (!api?.window_close) {
    isClosing.value = false;
    return;
  }
  try {
    const res = (await api.window_close()) as { ok?: boolean } | undefined;
    if (res && res.ok === false) isClosing.value = false;
  } catch {
    isClosing.value = false;
  }
}

async function onToggleMaximize() {
  const api = window.pywebview?.api;
  if (!api?.window_toggle_maximize) return;
  try {
    const res = await api.window_toggle_maximize();
    if (res && typeof res.maximized === "boolean") isMaximized.value = res.maximized;
  } catch {
    // js_api 调用失败静默：桌面壳不可用时按钮不响应，不影响页面
  }
}

function onTitleBarDblclick(event: MouseEvent) {
  // 双击按钮不应触发最大化；只响应标题栏空白区
  if ((event.target as HTMLElement)?.closest("button")) return;
  void onToggleMaximize();
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
      <button type="button" class="titlebar-btn titlebar-maximize" data-testid="titlebar-maximize" aria-label="最大化或还原" title="最大化 / 还原" @click="onToggleMaximize">
        <Square v-if="!isMaximized" data-testid="maximize-icon-square" :size="12" aria-hidden="true" />
        <Copy v-else data-testid="maximize-icon-restore" :size="12" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="titlebar-btn titlebar-close"
        :class="{ closing: isClosing }"
        data-testid="titlebar-close"
        aria-label="关闭"
        :aria-busy="isClosing || undefined"
        :title="isClosing ? '正在关闭…' : '关闭'"
        :disabled="isClosing"
        @click="onWindowClose"
      >
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

/* X 悬停红底白字：通用行为，明/暗/特殊主题一致（spec FR-022） */
.titlebar-btn.titlebar-close:hover {
  background: #e5484d;
  color: #fff;
}

/* T027 正在关闭：点 X 后保持红底白字（不随悬停消失），直至窗口退出 */
.titlebar-btn.titlebar-close.closing,
.titlebar-btn.titlebar-close.closing:hover {
  background: #e5484d;
  color: #fff;
}

/* 特殊主题大类（data-theme-category="special"：万花筒及后续特殊主题）：
   整条标题栏为深色毛玻璃磨砂（从右到左一整条，覆盖按钮 + 中间空白 +
   软件名），背后图案不可辨（FR-008/FR-009，用户 2026-09-02 真机反馈
   确认"整个条框全部都是磨砂"）。背景色加深至 0.82、模糊加大至 40px，
   对比度足以在 WebView2 下显眼可见。磨砂挂大类标记而非具体主题 id，
   新特殊主题自动继承（A9）。 */
[data-theme-category="special"] .window-titlebar {
  position: relative;
  z-index: 1;
  background: rgba(8, 11, 16, 0.82);
  backdrop-filter: blur(40px) saturate(1.5);
  -webkit-backdrop-filter: blur(40px) saturate(1.5);
  border-bottom-color: rgba(255, 255, 255, 0.08);
  padding-right: 0;
}

[data-theme-category="special"] .titlebar-name {
  color: rgba(255, 255, 255, 0.85);
}

/* 控制条还原为普通容器：磨砂作用在整条标题栏上，控制条本身不再单独限定宽度或磨砂 */
[data-theme-category="special"] .titlebar-controls {
  height: 100%;
  justify-content: flex-end;
  padding: 0 6px;
}

[data-theme-category="special"] .titlebar-btn {
  color: rgba(255, 255, 255, 0.92);
  border-radius: 0;
}

/* 最小化/最大化悬停变深色（spec FR-009） */
[data-theme-category="special"] .titlebar-btn:hover {
  background: rgba(0, 0, 0, 0.32);
}

/* X 悬停红底白字优先于深色 hover（FR-009） */
[data-theme-category="special"] .titlebar-btn.titlebar-close:hover {
  background: #e5484d;
  color: #fff;
}

/* T027 特殊主题下「正在关闭」同样保持红底白字（不随悬停消失） */
[data-theme-category="special"] .titlebar-btn.titlebar-close.closing,
[data-theme-category="special"] .titlebar-btn.titlebar-close.closing:hover {
  background: #e5484d;
  color: #fff;
}
</style>
