<script setup lang="ts">
// ===========================================================================
// 通用主题选择列表：从注册表读取选项，当前项以菱形指针标识。
// ===========================================================================
import { THEME_REGISTRY } from "./registry";

defineProps<{ current: string }>();

const emit = defineEmits<{ (e: "select", id: string): void }>();
</script>

<template>
  <div class="theme-options" role="listbox" aria-label="主题选择">
    <button
      v-for="theme in THEME_REGISTRY"
      :key="theme.id"
      type="button"
      role="option"
      class="theme-option"
      :class="[`sw-${theme.id}`, { current: theme.id === current }]"
      :aria-selected="theme.id === current"
      @click="emit('select', theme.id)"
    >
      <span class="theme-swatch" aria-hidden="true"></span>
      <span class="theme-option-text">
        <span class="theme-option-label">{{ theme.label }}</span>
        <span class="theme-option-desc">{{ theme.description }}</span>
      </span>
      <span
        v-if="theme.id === current"
        class="theme-option-current"
        aria-hidden="true"
      >◆</span>
    </button>
  </div>
</template>
