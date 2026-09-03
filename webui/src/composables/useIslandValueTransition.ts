// ---------------------------------------------------------------------------
// 灵动岛展示值切换：在新值进入时短暂保留旧值供上滑淡出。
//
// 这里只管理展示层的过渡状态，不拥有业务数据、不发请求；调用方通过
// enabled 在 prefers-reduced-motion 下关闭旧值保留，保证减少动态时直接替换。
// ---------------------------------------------------------------------------
import { onBeforeUnmount, ref, watch, type ComputedRef, type Ref } from "vue";

export interface IslandValueTransitionOptions {
  duration?: number;
  enabled?: () => boolean;
}

export function useIslandValueTransition(
  source: Ref<string> | ComputedRef<string>,
  options: IslandValueTransitionOptions = {},
): { exiting: Ref<string[]> } {
  const exiting = ref<string[]>([]);
  const duration = options.duration ?? 400;
  let clearTimer: ReturnType<typeof setTimeout> | undefined;

  function clearExiting(): void {
    if (clearTimer !== undefined) {
      clearTimeout(clearTimer);
      clearTimer = undefined;
    }
    exiting.value = [];
  }

  watch(source, (next, previous) => {
    if (!next || !previous || next === previous) {
      if (!next) clearExiting();
      return;
    }
    if (options.enabled?.() === false) {
      clearExiting();
      return;
    }
    if (clearTimer !== undefined) clearTimeout(clearTimer);
    exiting.value = [previous];
    clearTimer = setTimeout(() => {
      exiting.value = [];
      clearTimer = undefined;
    }, duration);
  });

  onBeforeUnmount(clearExiting);

  return { exiting };
}
