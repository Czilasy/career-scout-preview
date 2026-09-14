<script setup lang="ts">
// ---------------------------------------------------------------------------
// 036 B084 自绘标题栏（仅桌面 EXE 渲染）：窗口顶部与主题融合的标题栏。
//
// 渲染条件：window.pywebview 存在（浏览器模式不显示，spec FR-006）。
// 布局：左侧 Career Scout 文字 + 右侧最小化 / 最大化-还原 / 关闭三按钮。
// 窗口交互（036 v2，Research D9）：命中判定在页面（DOM 事件、CSS 像素），
//       命中后只发起**一次**调用，宿主用 Win32 完成拉伸/移动/还原——无边框
//       窗体的客户区被 WebView2 子窗口整块覆盖，宿主进程收不到任何原生
//       命中消息，页面是唯一入口。四角 → 四边 → 标题带 → 客户区；最大化
//       状态不上报边缘（不提供边缘拉伸），标题带仍上报移动以支持拖下还原，
//       三个按钮与按钮区一律按客户区处理。指针反馈用页面区域类名表达。
// 双击：标题栏容器显式绑定 dblclick → window_toggle_maximize（最大化/还原
//       仍走 js_api，图标随返回值同步）。
// 主题：浅色=白、暗色=暗；特殊主题大类（data-theme-category="special"，
//       万花筒及后续特殊主题）窗口控制条为半透明深色毛玻璃磨砂 + 强模糊，
//       X 悬停红底白字、最小化/最大化悬停变深色（spec FR-008/FR-009）。
//       磨砂样式挂在大类标记上而非具体主题 id，新特殊主题自动继承（A9）。
// 图标：最大化/还原按钮图标随窗口真实状态切换（FR-004）——最大化=单方框、
//       还原=重叠方块；初始挂载、toggle/双击后与窗口尺寸变化后同步。
// ---------------------------------------------------------------------------
import { Copy, Minus, Square, X } from "@lucide/vue";
import { onMounted, onUnmounted, ref } from "vue";

// 与 packaging/window_interaction.py 的 EDGE_CSS 保持一致：
// 页面按 CSS 像素判定，宿主按 DPI 换算物理像素（标题带不靠几何判定，
// 而是只认 [data-testid="titlebar-drag-region"] 上的按压）。
const EDGE_ZONE = 6;

type Region = "L" | "R" | "T" | "B" | "TL" | "TR" | "BL" | "BR";

function resolveRegion(x: number, y: number, maximized: boolean): Region | null {
  if (typeof window === "undefined") return null;
  if (maximized) return null; // 最大化不提供边缘拉伸（spec FR-001 边界）
  const width = window.innerWidth;
  const height = window.innerHeight;
  const left = x < EDGE_ZONE;
  const right = x >= width - EDGE_ZONE;
  const top = y < EDGE_ZONE;
  const bottom = y >= height - EDGE_ZONE;

  if (top && left) return "TL";
  if (top && right) return "TR";
  if (bottom && left) return "BL";
  if (bottom && right) return "BR";
  if (left) return "L";
  if (right) return "R";
  if (top) return "T";
  if (bottom) return "B";
  return null;
}

/** 只有 Windows 桌面壳（WebView2）才有本套原生窗口交互；macOS 保持原状（FR-019）。 */
function shellSupportsWindowInteraction(): boolean {
  if (typeof window === "undefined") return false;
  return window.pywebview?.platform === "edgechromium";
}

/** 标题带可拖动区：只认真按在标题带空白/标题文字上的按压（FR-008）。 */
const DRAG_REGION_SELECTOR = '[data-testid="titlebar-drag-region"]';

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

// ---------------------------------------------------------------------------
// 窗口交互（036 v2）：区域判定 → 一次调用；指针反馈；尺寸变化同步图标
// ---------------------------------------------------------------------------
let cursorRegion: Region | null = null;

function setCursorRegion(region: Region | null) {
  if (cursorRegion === region) return;
  cursorRegion = region;
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (region) {
    root.dataset.csRegion = region;
  } else {
    delete root.dataset.csRegion;
  }
}

function onDocumentMouseDown(event: MouseEvent) {
  if (!isDesktop.value || event.button !== 0) return;
  if (!shellSupportsWindowInteraction()) return;
  // 按钮、浮层与页面控件不参与窗口交互：直接交给页面自身处理
  const target = event.target as HTMLElement | null;
  const closest = typeof target?.closest === "function"
    ? (selector: string) => target.closest(selector)
    : () => null;
  if (closest("button, a, input, select, textarea, [role=\"button\"]")) return;

  const region = resolveRegion(event.clientX, event.clientY, isMaximized.value);
  const api = window.pywebview?.api;
  if (region !== null) {
    event.preventDefault();
    void api?.window_begin_resize?.(region, event.clientX, event.clientY);
    return;
  }
  // 标题带移动：必须真的按在标题带可拖动区（空白/标题文字），
  // 否则对话框遮罩、灵动岛等盖在顶部 36px 的内容会被误当标题带拖动。
  if (closest(DRAG_REGION_SELECTOR)) {
    event.preventDefault();
    void api?.window_begin_move?.(event.clientX, event.clientY);
  }
}

function onDocumentMouseMove(event: MouseEvent) {
  if (!isDesktop.value || !shellSupportsWindowInteraction()) return;
  setCursorRegion(
    resolveRegion(event.clientX, event.clientY, isMaximized.value),
  );
}

let resizeTimer: ReturnType<typeof setTimeout> | undefined;

function onWindowResize() {
  // 宿主执行还原/最大化后窗口尺寸会变化：防抖后回查真实最大化态，
  // 保证按钮图标不落后于窗口状态（FR-004）。
  if (resizeTimer !== undefined) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = undefined;
    void syncMaximized();
  }, 200);
}

onMounted(() => {
  if (typeof window === "undefined") return;
  document.addEventListener("mousedown", onDocumentMouseDown, true);
  document.addEventListener("mousemove", onDocumentMouseMove);
  window.addEventListener("resize", onWindowResize);
  if (window.pywebview) {
    isDesktop.value = true;
    void syncMaximized();
  } else {
    window.addEventListener("pywebviewready", handlePywebviewReady);
  }
});

onUnmounted(() => {
  if (typeof window !== "undefined") {
    document.removeEventListener("mousedown", onDocumentMouseDown, true);
    document.removeEventListener("mousemove", onDocumentMouseMove);
    window.removeEventListener("resize", onWindowResize);
    window.removeEventListener("pywebviewready", handlePywebviewReady);
    if (resizeTimer !== undefined) {
      clearTimeout(resizeTimer);
      resizeTimer = undefined;
    }
  }
  setCursorRegion(null);
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
    <span class="titlebar-drag" data-testid="titlebar-drag-region">
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

<style>
/* 036 v2 指针反馈（契约 §6）：区域类名挂在 <html data-cs-region>，命中边缘/
   角落时显示方向一致的缩放指针；不新增任何可见元素，不遮挡页面内容。
   使用全局规则覆盖子树自带 cursor，离开区域即移除属性。 */
[data-cs-region="L"], [data-cs-region="L"] * { cursor: w-resize !important; }
[data-cs-region="R"], [data-cs-region="R"] * { cursor: e-resize !important; }
[data-cs-region="T"], [data-cs-region="T"] * { cursor: n-resize !important; }
[data-cs-region="B"], [data-cs-region="B"] * { cursor: s-resize !important; }
[data-cs-region="TL"], [data-cs-region="TL"] * { cursor: nw-resize !important; }
[data-cs-region="TR"], [data-cs-region="TR"] * { cursor: ne-resize !important; }
[data-cs-region="BL"], [data-cs-region="BL"] * { cursor: sw-resize !important; }
[data-cs-region="BR"], [data-cs-region="BR"] * { cursor: se-resize !important; }
</style>
