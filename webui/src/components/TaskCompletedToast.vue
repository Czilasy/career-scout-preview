<script setup lang="ts">
defineProps<{ visible: boolean }>();
defineEmits<{ click: []; close: [] }>();
</script>

<template>
  <Transition name="toast">
    <div
      v-if="visible"
      class="task-completed-toast"
      data-testid="task-completed-toast"
    >
      <button
        type="button"
        class="task-completed-toast-main"
        data-testid="task-completed-toast-go"
        @click="$emit('click')"
      >
        <span class="task-completed-toast-title">本轮任务已完成</span>
        <span class="task-completed-toast-action">点击查看最新结果</span>
      </button>
      <button
        type="button"
        class="task-completed-toast-close"
        aria-label="关闭提示"
        data-testid="task-completed-toast-close"
        @click="$emit('close')"
      >
        ×
      </button>
    </div>
  </Transition>
</template>

<style scoped>
.task-completed-toast {
  position: fixed;
  top: 72px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 80;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px 6px 16px;
  border: 1px solid var(--brand-edge);
  border-radius: 999px;
  background: var(--panel);
  box-shadow: var(--shadow);
}

.task-completed-toast-main {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  border: 0;
  background: transparent;
  color: var(--ink-1);
  font: inherit;
  font-size: 0.9rem;
  cursor: pointer;
}

.task-completed-toast-title {
  font-weight: 600;
}

.task-completed-toast-action {
  color: var(--brand);
}

.task-completed-toast-close {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 50%;
  background: var(--hair);
  color: var(--ink-3);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.task-completed-toast-close:hover {
  color: var(--ink-1);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-8px);
}
</style>
