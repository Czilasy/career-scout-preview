<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

const props = defineProps<{
  open: boolean;
  batchInfo: { current: number; total: number } | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "choose", mode: "immediate" | "graceful"): void;
}>();

const immediateBtn = ref<HTMLButtonElement | null>(null);

// 025 B076：「立即停止」默认聚焦（回车可直接触发）；open 变化与初始挂载都生效
watch(
  () => props.open,
  async (open) => {
    if (open) {
      await nextTick();
      immediateBtn.value?.focus();
    }
  },
  { immediate: true },
);
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="pause-choice-overlay"
      data-testid="pause-batch-dialog"
      @click.self="emit('close')"
    >
      <div class="pause-choice-card" role="dialog" aria-modal="true" aria-label="暂停 AI 筛选">
        <h3 class="pause-choice-title">暂停 AI 筛选</h3>
        <!-- 025：平实提示，不渲染严重性、不吓用户 -->
        <p class="pause-choice-hint">
          立即停止将放弃当前批次已抓取的内容，下次继续时需要重新抓取这一批
        </p>
        <p v-if="batchInfo && batchInfo.total > 0" class="pause-choice-progress">
          当前进度：第 {{ batchInfo.current }}/{{ batchInfo.total }} 批
        </p>
        <div class="pause-choice-actions">
          <button
            ref="immediateBtn"
            type="button"
            class="pause-choice-btn primary"
            data-testid="pause-immediate"
            @click="emit('choose', 'immediate')"
          >
            立即停止
          </button>
          <button
            type="button"
            class="pause-choice-btn"
            data-testid="pause-graceful"
            @click="emit('choose', 'graceful')"
          >
            等这批抓完
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pause-choice-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}

.pause-choice-card {
  width: 360px;
  max-width: calc(100vw - 48px);
  padding: 20px 22px;
  border-radius: 10px;
  background: var(--dialog-bg, #fff);
  color: var(--text-color, #1f2329);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}

.pause-choice-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
}

.pause-choice-hint {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary, #646a73);
}

.pause-choice-progress {
  margin: 0 0 16px;
  font-size: 12px;
  color: var(--text-secondary, #646a73);
}

.pause-choice-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.pause-choice-btn {
  padding: 7px 16px;
  border: 1px solid var(--border-color, #d0d3d9);
  border-radius: 6px;
  background: transparent;
  color: var(--text-color, #1f2329);
  font-size: 13px;
  cursor: pointer;
}

.pause-choice-btn.primary {
  border-color: var(--accent-color, #3370ff);
  background: var(--accent-color, #3370ff);
  color: #fff;
}
</style>
