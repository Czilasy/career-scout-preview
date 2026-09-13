import { nextTick, onBeforeUnmount, onMounted, watch, type Ref } from "vue";

const DEFAULT_MIN_HEIGHT = 96;

/**
 * Spec041：自动高度可接现场存档（页面内现场保留）。
 *
 * 契约（spec FR-001「画像输入框已撑开的高度」保留）：
 * - 同一轮次、同一窗口宽度、同一内容 → 接回存档高度（不重算，避免闪动）；
 * - 页面重新可见 / 内容变化 / 窗口宽度变化 → 重新计算，并把最新高度写回现场；
 * - 切画像 / 开新轮 / 切平台 → 身份键换了，新身份没存档 → 按当前内容重算。
 * 页面隐藏（display:none / v-show 未显示）时量不到真实高度，不回写存档。
 */
export interface AutoGrowSceneHooks {
  /** 页面可见标记：重新可见时重算或接回现场。 */
  visible?: Ref<boolean>;
  /** 读取现场尺寸（同宽同内容才采用）；无现场返回 null。 */
  readScene?: () => { height: number | null; width: number | null; content: string | null };
  /** 计算完成后回写现场尺寸。 */
  writeScene?: (height: number, width: number, content: string) => void;
}

export function useAutoGrowTextarea(
  input: Ref<HTMLTextAreaElement | null>,
  value: Ref<string>,
  minHeight = DEFAULT_MIN_HEIGHT,
  hooks: AutoGrowSceneHooks = {},
) {
  function resize() {
    const element = input.value;
    if (!element) return;
    const width = element.clientWidth || 0;
    const content = value.value;
    const saved = hooks.readScene?.() ?? null;
    if (width > 0 && saved?.height && saved.width === width && saved.content === content) {
      element.style.height = `${saved.height}px`;
      return;
    }
    element.style.height = "auto";
    const height = Math.max(element.scrollHeight, minHeight);
    element.style.height = `${height}px`;
    // 宽度为 0 表示当前不可见（v-show 隐藏时量不到高度），等可见时再算再写。
    if (width > 0) hooks.writeScene?.(height, width, content);
  }

  function scheduleResize() {
    void nextTick(resize);
  }

  function handleWindowResize() {
    scheduleResize();
  }

  onMounted(() => {
    window.addEventListener("resize", handleWindowResize);
    scheduleResize();
  });

  onBeforeUnmount(() => {
    window.removeEventListener("resize", handleWindowResize);
  });

  watch(value, scheduleResize);
  if (hooks.visible) watch(hooks.visible, (isVisible) => {
    if (isVisible) scheduleResize();
  });

  return { scheduleResize };
}
