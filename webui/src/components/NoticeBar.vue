<script setup lang="ts">
import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from "@lucide/vue";
import type { Notice } from "../types";

defineProps<{ notice: Notice | null }>();
defineEmits<{ dismiss: [] }>();

const icons = {
  info: Info,
  success: CheckCircle2,
  warning: TriangleAlert,
  error: AlertCircle,
};
</script>

<template>
  <div
    v-if="notice"
    class="notice-bar"
    :data-tone="notice.tone"
    role="status"
    aria-live="polite"
  >
    <component :is="icons[notice.tone]" :size="18" aria-hidden="true" />
    <span>{{ notice.message }}</span>
    <button class="icon-button" type="button" aria-label="关闭提示" @click="$emit('dismiss')">
      <X :size="17" />
    </button>
  </div>
</template>
