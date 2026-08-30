<script setup lang="ts">
// ===========================================================================
// 主题长按弹层选项列表（032）：亮 / 暗 / 万花筒 三项。
// 标本块即预览：亮＝白玻璃切片、暗＝黑玻璃棱线、万花筒＝流动微缩 rosette
// （三枚里唯一"活的"，扫一眼即知哪项是彩蛋）。当前项以菱形指针标识。
// ===========================================================================
import { THEME_REGISTRY } from "./registry";

defineProps<{ current: string }>();

const emit = defineEmits<{ (e: "select", id: string): void }>();

const ROSETTE_ARMS = [0, 60, 120, 180, 240, 300];
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
      <span class="theme-swatch" aria-hidden="true">
        <svg
          v-if="theme.id === 'kaleido'"
          class="kaleido-rosette"
          viewBox="0 0 24 24"
        >
          <defs>
            <linearGradient
              id="kaleido-rosette-spectrum"
              x1="0"
              y1="0"
              x2="1"
              y2="1"
            >
              <stop offset="0" stop-color="#ff8a7a" />
              <stop offset="0.25" stop-color="#ffc94d" />
              <stop offset="0.5" stop-color="#5effa0" />
              <stop offset="0.75" stop-color="#5f9bff" />
              <stop offset="1" stop-color="#d06cff" />
            </linearGradient>
          </defs>
          <g fill="none" stroke="url(#kaleido-rosette-spectrum)" stroke-width="1.1">
            <path
              v-for="arm in ROSETTE_ARMS"
              :key="arm"
              d="M12 3 14.6 9.4 12 15.8 9.4 9.4Z"
              :transform="`rotate(${arm} 12 12)`"
            />
          </g>
          <circle cx="12" cy="12" r="1.6" fill="#f0f6fa" />
        </svg>
      </span>
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
