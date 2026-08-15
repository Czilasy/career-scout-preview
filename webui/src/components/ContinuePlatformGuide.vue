<script setup lang="ts">
import type { Platform } from "../types";

defineProps<{
  guide: { boss: boolean; zhilian: boolean };
}>();

const emit = defineEmits<{
  choose: [platform: Platform];
  cancel: [];
}>();
</script>

<template>
  <div class="recrawl-guide" data-testid="continue-platform-guide" role="dialog" aria-label="选择继续筛选平台">
    <p class="recrawl-guide-title">选择要继续 AI 筛选的平台</p>
    <div class="recrawl-guide-actions">
      <button type="button" class="button secondary small" data-testid="continue-choose-boss" :disabled="!guide.boss" @click="emit('choose', 'boss')">继续 BOSS</button>
      <button type="button" class="button secondary small" data-testid="continue-choose-zhilian" :disabled="!guide.zhilian" @click="emit('choose', 'zhilian')">继续 智联</button>
      <button type="button" class="button danger small" data-testid="continue-guide-cancel" @click="emit('cancel')">取消</button>
    </div>
  </div>
</template>
