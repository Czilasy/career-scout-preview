/** 045 v2 关闭确认编排：桌面壳请求 → 页面弹自绘框 → 结果回传。

壳侧在 `closing` 事件里调 `window.__csCloseConfirm({ scenario })`，本模块弹框
并返回一个 Promise；用户点「立即结束」resolve `confirm`，取消 resolve `cancel`。
壳侧据此决定落轮后关窗还是保持现场。

场景判定在壳侧完成（单一职责），这里只负责呈现与回传。浏览器模式不会安装
入口——网页版不做任何关闭拦截（FR-016）。
*/

import { ref } from "vue";

/** 关闭场景：未运行但有岗位 / 运行中且有岗位。 */
export type CloseScenario = "idle_save" | "running_stop";

/** 回传给壳侧的动作。 */
export type CloseAction = "confirm" | "cancel";

export function useCloseConfirm() {
  const open = ref(false);
  const scenario = ref<CloseScenario>("idle_save");
  const busy = ref(false);
  let resolver: ((action: CloseAction) => void) | null = null;

  function settle(action: CloseAction) {
    const pending = resolver;
    resolver = null;
    pending?.(action);
  }

  /** 壳侧请求弹框。已开框或收尾进行中时直接回 cancel，不打断进行中的收尾。 */
  function request(next: CloseScenario): Promise<CloseAction> {
    if (resolver || busy.value) {
      return Promise.resolve<CloseAction>("cancel");
    }
    scenario.value = next;
    busy.value = false;
    open.value = true;
    return new Promise<CloseAction>((resolve) => {
      resolver = resolve;
    });
  }

  /** 用户选择结束：进入等待态并保持打开，直到壳侧关窗。 */
  function confirm() {
    if (!resolver || busy.value) return;
    busy.value = true;
    settle("confirm");
  }

  /** 用户放弃关闭（右上 ✕ / Esc）：保持现场。 */
  function cancel() {
    if (!resolver) {
      open.value = false;
      busy.value = false;
      return;
    }
    open.value = false;
    busy.value = false;
    settle("cancel");
  }

  return { open, scenario, busy, request, confirm, cancel };
}

export type CloseConfirmFlow = ReturnType<typeof useCloseConfirm>;
