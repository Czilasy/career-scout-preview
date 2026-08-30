// 031 B8：档位/规模风险警示文案（自 DiscoveryView.vue 原样抽出，逻辑零改动）。
//
// 024 的档位/规模风险警示（黄色警示区数据源）：
// - 极限警告：仅极限档；
// - 大任务警告：任何档位，按新口径总页数 >30（scopePreview 未确认时为 false）。
// 警示并入「当前模式」说明行内，不占独立警告框。

import { computed, type Ref } from "vue";
import type { ExecutionSelection, FrozenSearchScope } from "../types";

export function useModeWarnings(
  executionSelection: Ref<ExecutionSelection>,
  scopePreview: Ref<FrozenSearchScope | null>,
) {
  const extremeWarning = computed(() => executionSelection.value === "extreme");
  const largeTaskWarning = computed(() => Boolean(
    scopePreview.value?.planned_pages && scopePreview.value.planned_pages > 30,
  ));
  const modeWarnings = computed(() => {
    const list: string[] = [];
    if (extremeWarning.value) list.push("有概率限流");
    if (largeTaskWarning.value) list.push("任务规模过大可能封号");
    return list;
  });
  return modeWarnings;
}
