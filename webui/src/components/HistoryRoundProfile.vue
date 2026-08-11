<script setup lang="ts">
import { ref } from "vue";
import { FileText } from "@lucide/vue";

defineProps<{
  profileText: string;
}>();

const open = ref(false);
</script>

<template>
  <div
    class="history-profile-hover"
    data-testid="history-round-profile"
    @mouseenter="open = true"
    @mouseleave="open = false"
  >
    <span
      class="history-round-profile-trigger"
      tabindex="0"
      role="button"
      aria-label="查看本轮画像"
      :aria-expanded="open"
      data-testid="history-round-profile-trigger"
      @focus="open = true"
      @blur="open = false"
    >
      <FileText :size="15" aria-hidden="true" />
      <span class="history-profile-trigger-text">本轮画像</span>
    </span>

    <Transition name="profile-popover">
      <div
        v-if="open"
        class="history-round-profile-popover"
        data-testid="history-round-profile-body"
      >
        <p v-if="profileText.trim()">{{ profileText }}</p>
        <p v-else class="history-round-profile-empty">未记录完整画像</p>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.history-profile-hover {
  position: relative;
  display: inline-flex;
}

.history-round-profile-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid var(--hair);
  border-radius: 999px;
  background: var(--panel);
  color: var(--brand-ink);
  font-size: 0.84rem;
  font-weight: 600;
  white-space: nowrap;
  cursor: default;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.history-round-profile-trigger:hover,
.history-round-profile-trigger:focus-visible {
  border-color: var(--brand);
  background: var(--brand-wash);
}

.history-round-profile-popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 40;
  width: min(340px, calc(100vw - 32px));
  max-height: min(46vh, 340px);
  padding: 12px 14px;
  overflow-y: auto;
  border: 1px solid var(--brand-edge);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: var(--shadow);
  color: var(--text-soft);
  font-size: 0.9rem;
  line-height: 1.65;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.history-round-profile-popover p {
  margin: 0;
}

.history-round-profile-empty {
  color: var(--text-muted);
}

.profile-popover-enter-active,
.profile-popover-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.profile-popover-enter-from,
.profile-popover-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.profile-popover-leave-active {
  pointer-events: none;
}

@media (max-width: 760px) {
  .history-round-profile-trigger {
    width: 44px;
    height: 44px;
    min-height: 44px;
    justify-content: center;
    padding: 0;
    font-size: 0;
  }
  .history-round-profile-popover {
    right: auto;
    left: max(12px, calc(50vw - 170px));
    position: fixed;
  }
}
</style>
