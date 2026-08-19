<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Check, LoaderCircle, RotateCcw } from "@lucide/vue";

const props = defineProps<{
  count: number;
  busy?: boolean;
}>();

const emit = defineEmits<{
  recrawl: [];
  dismiss: [];
}>();

// 全部处理完时短暂展示绿色对勾，然后自动收掉。
const done = ref(false);
// 用户点「暂不处理」后隐藏横幅，直到待处理数归零（新一轮/真正清空）再复位。
const dismissed = ref(false);
let doneTimer: number | undefined;

watch(
  () => props.count,
  (next, prev) => {
    if (typeof prev === "number" && prev > 0 && next === 0) {
      done.value = true;
      dismissed.value = false;
      if (doneTimer) window.clearTimeout(doneTimer);
      doneTimer = window.setTimeout(() => {
        done.value = false;
      }, 1200);
    }
  },
);

onBeforeUnmount(() => {
  if (doneTimer) window.clearTimeout(doneTimer);
});

const showPending = computed(() => !done.value && !dismissed.value && props.count > 0);

function onDismiss() {
  dismissed.value = true;
  emit("dismiss");
}
</script>

<template>
  <Transition name="capsule" appear>
    <div
      v-if="showPending"
      class="pending-capsule"
      data-testid="pending-recrawl-capsule"
      role="status"
    >
      <span class="pending-capsule-message">
        还有 <span class="pending-capsule-count">{{ count }}</span> 个岗位未处理完
      </span>
      <div class="pending-capsule-actions">
        <button
          class="pending-capsule-skip"
          type="button"
          data-testid="pending-recrawl-dismiss"
          :disabled="busy"
          @click="onDismiss"
        >
          暂不处理
        </button>
        <button
          class="pending-capsule-action"
          type="button"
          data-testid="pending-recrawl"
          :disabled="busy"
          @click="emit('recrawl')"
        >
        <LoaderCircle
          v-if="busy"
          class="spin pending-capsule-icon"
          :size="16"
          aria-hidden="true"
        />
        <RotateCcw v-else class="pending-capsule-icon" :size="16" aria-hidden="true" />
        全部重抓（{{ count }}）
      </button>
      </div>
    </div>
    <div
      v-else-if="done"
      class="pending-capsule pending-capsule--done"
      data-testid="pending-recrawl-done"
      role="status"
    >
      <Check class="pending-capsule-done-icon" :size="18" aria-hidden="true" />
      <span>待确认岗位已全部处理</span>
    </div>
  </Transition>
</template>

<style scoped>
.pending-capsule {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 12px;
  min-height: 52px;
  padding: 8px 12px 8px 16px;
  border: 1px solid var(--unsure-edge);
  border-radius: 8px;
  background: var(--unsure-wash);
  color: var(--unsure-deep);
  animation: capsule-enter 240ms ease both;
}

.pending-capsule-message {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.pending-capsule-count {
  min-width: 24px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--unsure);
  color: var(--on-bright-bg, #ffffff);
  font-size: 13px;
  font-weight: 700;
  text-align: center;
  animation: count-breathe 2.4s ease-in-out infinite;
}

.pending-capsule-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 12px;
  border: 1px solid var(--unsure-edge);
  border-radius: 7px;
  background: var(--panel);
  color: var(--unsure-deep);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 160ms ease, border-color 160ms ease, color 160ms ease, background 160ms ease;
}
.pending-capsule-action:hover,
.pending-capsule-action:focus-visible {
  transform: translateY(-1px);
  border-color: var(--unsure);
  background: var(--panel-2);
}
.pending-capsule-action:hover .pending-capsule-icon,
.pending-capsule-action:focus-visible .pending-capsule-icon {
  transform: translateX(2px);
}
.pending-capsule-action:disabled {
  cursor: not-allowed;
  opacity: .78;
}

.pending-capsule-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.pending-capsule-skip {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--unsure-deep);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: color 160ms ease, background 160ms ease, border-color 160ms ease;
}
.pending-capsule-skip:hover,
.pending-capsule-skip:focus-visible {
  background: var(--panel-2, rgba(127, 127, 127, 0.12));
  border-color: var(--unsure-edge);
}
.pending-capsule-skip:disabled {
  cursor: not-allowed;
  opacity: .6;
}
.pending-capsule-icon {
  flex: 0 0 auto;
  transition: transform 160ms ease;
}

.pending-capsule--done {
  border-color: var(--match-edge);
  background: var(--match-wash);
  color: var(--match-deep);
  animation: capsule-enter 200ms ease both, done-collapse 360ms ease 640ms both;
}
.pending-capsule-done-icon {
  flex: 0 0 auto;
}

.capsule-enter-active,
.capsule-leave-active {
  transition: opacity 200ms ease, transform 200ms ease;
}
.capsule-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}
.capsule-enter-to {
  opacity: 1;
  transform: translateY(0);
}
.capsule-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(.98);
}

@keyframes capsule-enter {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes count-breathe {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--unsure) 26%, transparent);
  }
  50% {
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--unsure) 16%, transparent);
  }
}

@keyframes done-collapse {
  to {
    opacity: 0;
    transform: translateY(-4px) scale(.98);
  }
}
</style>
