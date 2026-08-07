<script setup lang="ts">
// 列表头部工具栏：筛选 / 排序两个图标按钮 + 浮层。
// - 浮层锚定各自按钮，开合互斥（开一个关另一个）；fixed 定位避开列表容器 overflow 裁剪。
// - 关闭路径：点外部 / ESC / 点按钮 / 操作完成（排序点选、筛选确定/重置）。
// - 筛选面板使用草稿（确定才提交）；排序点选即生效。
// - 窄屏（≤390px）按钮退化为纯图标，徽标保留数字。
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ArrowDownUp, SlidersHorizontal } from "@lucide/vue";
import JobFilterPanel from "./JobFilterPanel.vue";
import JobSortMenu from "./JobSortMenu.vue";
import { SORT_OPTIONS, countActiveFilters, emptyFilterState } from "../listFilter";
import type { FilterState, SortKey } from "../listFilter";

const props = defineProps<{
  filterState: FilterState;
  sortKey: SortKey;
}>();

const emit = defineEmits<{
  "apply-filter": [state: FilterState];
  "reset-filter": [];
  "select-sort": [key: SortKey];
}>();

const filterPanelOpen = ref(false);
const sortMenuOpen = ref(false);
/** 筛选草稿：打开面板时从已应用状态复制，确定/重置才提交。 */
const filterDraft = ref<FilterState>(emptyFilterState());
const filterPanelEl = ref<HTMLElement | null>(null);
const sortMenuEl = ref<HTMLElement | null>(null);
const toolsEl = ref<HTMLElement | null>(null);

const filterCount = computed(() => countActiveFilters(props.filterState));
const activeFilterLabel = computed(() => (filterCount.value ? `筛选 ${filterCount.value}` : "筛选"));
const sortLabel = computed(
  () => SORT_OPTIONS.find((option) => option.key === props.sortKey)?.label ?? "综合排序",
);

function closePopovers() {
  filterPanelOpen.value = false;
  sortMenuOpen.value = false;
}

let scrollCleanup: (() => void) | undefined;

function openPopover(
  which: "filter" | "sort",
  anchor: HTMLElement,
  getPopover: () => HTMLElement | null,
) {
  if (which === "filter") {
    filterPanelOpen.value = true;
    sortMenuOpen.value = false;
  } else {
    sortMenuOpen.value = true;
    filterPanelOpen.value = false;
  }
  // 任意容器滚动时关闭浮层，避免 fixed 定位漂移
  if (scrollCleanup) scrollCleanup();
  const onScroll = () => { closePopovers(); };
  window.addEventListener("scroll", onScroll, { capture: true, passive: true });
  scrollCleanup = () => window.removeEventListener("scroll", onScroll, { capture: true });
  // v-if 渲染完成后才拿得到浮层元素做定位
  void nextTick(() => {
    const popover = getPopover();
    if (popover) positionPopover(anchor, popover);
  });
}

function positionPopover(anchor: HTMLElement, popover: HTMLElement) {
  const rect = anchor.getBoundingClientRect();
  const width = popover.offsetWidth;
  let left = Math.min(rect.right - width, window.innerWidth - width - 12);
  left = Math.max(12, left);
  popover.style.left = `${left}px`;
  const fitsBelow = rect.bottom + 8 + popover.offsetHeight <= window.innerHeight - 8;
  popover.style.top = fitsBelow
    ? `${rect.bottom + 8}px`
    : `${Math.max(8, rect.top - popover.offsetHeight - 8)}px`;
}

function onFilterToggle(event: MouseEvent) {
  const anchor = event.currentTarget as HTMLElement;
  if (filterPanelOpen.value) {
    closePopovers();
    return;
  }
  // 草稿始终从已应用状态派生（面板可反复开关，未点确定不丢失/不生效）
  filterDraft.value = {
    salary: [...props.filterState.salary],
    experience: [...props.filterState.experience],
    degree: [...props.filterState.degree],
    welfare: [...props.filterState.welfare],
  };
  openPopover("filter", anchor, () => filterPanelEl.value);
}

function onSortToggle(event: MouseEvent) {
  const anchor = event.currentTarget as HTMLElement;
  if (sortMenuOpen.value) {
    closePopovers();
    return;
  }
  openPopover("sort", anchor, () => sortMenuEl.value);
}

function applyFilters() {
  emit("apply-filter", {
    salary: [...filterDraft.value.salary],
    experience: [...filterDraft.value.experience],
    degree: [...filterDraft.value.degree],
    welfare: [...filterDraft.value.welfare],
  });
  closePopovers();
}

function resetFilters() {
  emit("reset-filter");
  closePopovers();
}

function selectSort(key: SortKey) {
  emit("select-sort", key);
  closePopovers();
}

function onDocumentPointerDown(event: MouseEvent) {
  if (!filterPanelOpen.value && !sortMenuOpen.value) return;
  const target = event.target as Node;
  const inFilter = filterPanelEl.value?.contains(target);
  const inSort = sortMenuEl.value?.contains(target);
  // 工具按钮本身由 click 处理（开/关切换），这里只关闭面板外点击
  const inTools = toolsEl.value?.contains(target);
  if (inFilter || inSort || inTools) return;
  closePopovers();
}

function onPopoverKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closePopovers();
}

onMounted(() => {
  document.addEventListener("pointerdown", onDocumentPointerDown);
  document.addEventListener("keydown", onPopoverKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointerDown);
  document.removeEventListener("keydown", onPopoverKeydown);
  if (scrollCleanup) scrollCleanup();
});
</script>

<template>
  <div ref="toolsEl" class="job-list-tools">
    <button
      type="button"
      class="list-tool-btn"
      :class="{ active: filterPanelOpen || filterCount > 0 }"
      data-testid="result-filter-toggle"
      aria-haspopup="dialog"
      :aria-expanded="filterPanelOpen"
      :title="activeFilterLabel"
      @click="onFilterToggle"
    >
      <SlidersHorizontal :size="15" aria-hidden="true" />
      <span
        v-if="filterCount > 0"
        class="list-tool-label"
        data-testid="result-filter-label"
      >筛选 {{ filterCount }}</span>
      <span v-if="filterCount > 0" class="list-tool-badge" data-testid="result-filter-badge">{{ filterCount }}</span>
    </button>
    <button
      type="button"
      class="list-tool-btn"
      :class="{ active: sortMenuOpen || sortKey !== 'default' }"
      data-testid="result-sort-toggle"
      aria-haspopup="menu"
      :aria-expanded="sortMenuOpen"
      :title="`排序：${sortLabel}`"
      @click="onSortToggle"
    >
      <ArrowDownUp :size="15" aria-hidden="true" />
      <span
        v-if="sortKey !== 'default'"
        class="list-tool-label"
        data-testid="result-sort-label"
      >{{ sortLabel }}</span>
    </button>

    <div
      v-if="filterPanelOpen"
      ref="filterPanelEl"
      class="filter-popover"
      data-testid="result-filter-panel"
      role="dialog"
      aria-modal="false"
      aria-label="筛选岗位"
    >
      <JobFilterPanel
        :model-value="filterDraft"
        @update:model-value="(state) => { filterDraft = state; }"
        @apply="applyFilters"
        @reset="resetFilters"
      />
    </div>

    <div
      v-if="sortMenuOpen"
      ref="sortMenuEl"
      class="sort-popover"
      data-testid="result-sort-menu"
      role="menu"
      aria-label="排序方式"
    >
      <JobSortMenu :model-value="sortKey" @select="selectSort" />
    </div>
  </div>
</template>