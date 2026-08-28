<script setup lang="ts">
import BaseDialog from "./BaseDialog.vue";

defineProps<{
  open: boolean;
  batchInfo: { current: number; total: number } | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "choose", mode: "immediate" | "graceful"): void;
}>();
</script>

<template>
  <!-- 025 B076 批中暂停二选一；025 修复：补取消（Esc/X/遮罩同义），样式走全局
       dialog/button 令牌，四套主题（明暗 × boss/zhilian）自动适配 -->
  <BaseDialog
    :open="open"
    title="暂停 AI 筛选"
    description="立即停止将放弃当前批次已抓取的内容，下次继续时需要重新抓取这一批"
    size="sm"
    initial-focus="[data-testid='pause-immediate']"
    id="pause-batch-choice"
    data-testid="pause-batch-dialog"
    @close="emit('close')"
  >
    <p v-if="batchInfo && batchInfo.total > 0" class="pause-choice-progress">
      当前进度：第 {{ batchInfo.current }}/{{ batchInfo.total }} 批
    </p>
    <template #footer>
      <button
        type="button"
        class="button ghost"
        data-testid="pause-cancel"
        @click="emit('close')"
      >
        取消
      </button>
      <button
        type="button"
        class="button secondary"
        data-testid="pause-graceful"
        @click="emit('choose', 'graceful')"
      >
        等这批抓完
      </button>
      <button
        type="button"
        class="button primary"
        data-testid="pause-immediate"
        @click="emit('choose', 'immediate')"
      >
        立即停止
      </button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.pause-choice-progress {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
</style>
