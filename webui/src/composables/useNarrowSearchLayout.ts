// 031 B8：窄屏布局媒体查询（自 DiscoveryView.vue 原样抽出，逻辑零改动）。
//
// 方案2（两个配置抽屉）的断点判定：宽屏两卡联动、窄屏各自独立。
// 阈值与 CSS `@media (max-width: 1050px)` 单列断点对齐，改一处必须同步另一处。

import { ref } from "vue";

type LayoutMediaListener = (e: { matches: boolean }) => void;

interface LayoutMedia {
  matches: boolean;
  addEventListener: (type: string, cb: LayoutMediaListener) => void;
}

function createSearchLayoutMedia(): LayoutMedia | null {
  if (typeof window.matchMedia !== "function") return null;
  const mq = window.matchMedia("(max-width: 1050px)");
  return {
    get matches() {
      return mq.matches;
    },
    addEventListener(type, cb) {
      if (typeof mq.addEventListener === "function") {
        mq.addEventListener(type, cb as unknown as EventListener);
      } else if (typeof mq.addListener === "function") {
        mq.addListener(cb as unknown as EventListener);
      }
    },
  };
}

/** 当前是否窄屏（≤1050px）布局；订阅 change 实时跟随。 */
export function useNarrowSearchLayout() {
  const searchLayoutMq = createSearchLayoutMedia();
  const isNarrowSearchLayout = ref(Boolean(searchLayoutMq?.matches));
  function handleSearchLayoutChange(e: { matches: boolean }): void {
    isNarrowSearchLayout.value = e.matches;
  }
  searchLayoutMq?.addEventListener("change", handleSearchLayoutChange);
  return isNarrowSearchLayout;
}
