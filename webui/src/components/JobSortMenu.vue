<script setup lang="ts">
// 排序浮层菜单：按 SORT_OPTIONS 渲染单选列表；数据暂缺项（published_desc /
// match_desc）置灰不可点，title 提示「数据补齐后开放」；点选即生效并关闭。
import { Check } from "@lucide/vue";
import { SORT_OPTIONS } from "../listFilter";
import type { SortKey } from "../listFilter";

defineProps<{
  modelValue: SortKey;
}>();

const emit = defineEmits<{
  select: [key: SortKey];
}>();
</script>

<template>
  <button
    v-for="option in SORT_OPTIONS"
    :key="option.key"
    type="button"
    role="menuitemradio"
    class="sort-option"
    :class="{ selected: modelValue === option.key }"
    :aria-checked="modelValue === option.key"
    :disabled="option.disabled"
    :title="option.disabled ? option.note : undefined"
    data-testid="sort-option"
    @click="emit('select', option.key)"
  >
    <span>{{ option.label }}</span>
    <Check v-if="modelValue === option.key" :size="15" aria-hidden="true" />
  </button>
</template>