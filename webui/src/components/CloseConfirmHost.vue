<script setup lang="ts">
import { onMounted } from "vue";
import PauseBatchChoiceDialog from "./PauseBatchChoiceDialog.vue";
import { useCloseConfirm, type CloseAction } from "../composables/useCloseConfirm";

/** 045 v2 关闭确认宿主：由 main.ts 以独立 Vue 实例挂在 body 上。
 *
 * 不进任何页面组件——App.vue 已过规模预警线，且确认框本来就与页面无关。
 * 桌面模式把 Promise 入口挂到 window（契约见 045 v2
 * contracts/close-confirm-bridge.md）并向壳注册就绪；浏览器模式什么都不
 * 装——网页版不做任何关闭拦截（FR-016），也不注册关闭拦截。
 */

type CloseBridgeWindow = Window & {
  __csCloseConfirm?: (payload?: { scenario?: string }) => Promise<CloseAction>;
  pywebview?: {
    api?: { register_close_ready?: () => Promise<unknown> };
  };
};

const { open, scenario, busy, request, confirm, cancel } = useCloseConfirm();

function install(): boolean {
  const win = window as CloseBridgeWindow;
  if (!win.pywebview?.api) return false;
  win.__csCloseConfirm = (payload?: { scenario?: string }) =>
    request(payload?.scenario === "running_stop" ? "running_stop" : "idle_save");
  void win.pywebview.api.register_close_ready?.();
  return true;
}

onMounted(() => {
  // pywebview 异步注入（晚于 Vue 挂载），就绪事件补挂一次
  if (!install()) {
    window.addEventListener("pywebviewready", install, { once: true });
  }
});
</script>

<template>
  <PauseBatchChoiceDialog
    :open="open"
    :kind="scenario === 'running_stop' ? 'close' : 'close_save'"
    :busy="busy"
    :batch-info="null"
    @choose="confirm"
    @close="cancel"
  />
</template>
