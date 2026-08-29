<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { Activity, Bot, LoaderCircle, Rocket, ScrollText, UserRound } from "@lucide/vue";
import LogViewerDialog from "./LogViewerDialog.vue";

const props = defineProps<{
  open: boolean;
  hasUpdate: boolean;
  updateVersion: string;
  checking?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  "open-ai-settings": [];
  "open-browser-accounts": [];
  "open-env-check": [];
  "manual-update-check": [];
  "open-github": [];
}>();

const menuEl = ref<HTMLElement | null>(null);
const firstItemEl = ref<HTMLButtonElement | null>(null);
const logsOpen = ref(false);

function onDocPointerDown(event: PointerEvent) {
  if (!props.open) return;
  const target = event.target as Node | null;
  if (!target || !menuEl.value?.contains(target)) emit("close");
}

function onDocKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && props.open) {
    event.preventDefault();
    emit("close");
  }
}

watch(() => props.open, (open) => {
  if (open) {
    nextTick(() => firstItemEl.value?.focus());
    document.addEventListener("pointerdown", onDocPointerDown);
    document.addEventListener("keydown", onDocKeydown);
  } else {
    document.removeEventListener("pointerdown", onDocPointerDown);
    document.removeEventListener("keydown", onDocKeydown);
  }
}, { immediate: true });

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocPointerDown);
  document.removeEventListener("keydown", onDocKeydown);
});
</script>

<template>
  <Transition name="settings-menu">
    <div
      v-if="open"
      ref="menuEl"
      class="settings-menu"
      data-testid="settings-menu"
      role="menu"
      aria-label="设置"
    >
      <button
        ref="firstItemEl"
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="ai-settings-trigger"
        @click="emit('open-ai-settings')"
      >
        <Bot :size="17" aria-hidden="true" /><span>AI 设置</span>
      </button>
      <button
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="browser-accounts-trigger"
        @click="emit('open-browser-accounts')"
      >
        <UserRound :size="17" aria-hidden="true" /><span>浏览器与账号</span>
      </button>
      <button
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="env-check-trigger"
        @click="emit('open-env-check')"
      >
        <Activity :size="17" aria-hidden="true" /><span>环境检查</span>
      </button>
      <button
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="logs-trigger"
        @click="logsOpen = true"
      >
        <ScrollText :size="17" aria-hidden="true" /><span>日志</span>
      </button>
      <button
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="manual-update-check"
        :disabled="checking"
        @click="emit('manual-update-check')"
      >
        <LoaderCircle v-if="checking" class="spin" :size="17" aria-hidden="true" />
        <Rocket v-else :size="17" aria-hidden="true" />
        <span>{{ checking ? "检查中…" : `检查更新${hasUpdate ? ` · v${updateVersion}` : ""}` }}</span>
      </button>
      <button
        class="settings-menu-item"
        type="button"
        role="menuitem"
        data-testid="github-link"
        @click="emit('open-github')"
      >
        <svg width="17" height="17" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
        </svg>
        <span>GitHub 仓库</span>
      </button>
    </div>
  </Transition>
  <LogViewerDialog :open="logsOpen" @close="logsOpen = false" />
</template>

<style scoped>
.settings-menu {
  position: fixed;
  top: 64px;
  right: 12px;
  z-index: 70;
  display: flex;
  flex-direction: column;
  width: 230px;
  padding: 6px;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: var(--shadow);
}

.settings-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.settings-menu-item:hover,
.settings-menu-item:focus-visible {
  background: var(--hair-2);
}

@media (max-width: 720px) {
  .settings-menu {
    right: 8px;
    width: min(230px, calc(100vw - 16px));
  }
}
</style>
