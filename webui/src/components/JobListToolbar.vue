<script setup lang="ts">
// 列表头部工具栏：筛选 / 排序两个图标按钮 + 浮层。
// - 浮层锚定各自按钮，开合互斥（开一个关另一个）；fixed 定位避开列表容器 overflow 裁剪。
// - 关闭路径：点外部 / ESC / 点按钮 / 操作完成（排序点选、筛选确定/重置）。
// - 筛选面板使用草稿（确定才提交）；排序点选即生效。
// - 窄屏（≤390px）按钮退化为纯图标，徽标保留数字。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ArrowDownUp, SlidersHorizontal } from "@lucide/vue";
import JobFilterPanel from "./JobFilterPanel.vue";
import JobSortMenu from "./JobSortMenu.vue";
import { SORT_OPTIONS, countActiveFilters, emptyFilterState } from "../listFilter";
import type { FilterState, SortKey } from "../listFilter";

const props = defineProps<{
  filterState: FilterState;
  draftFilterState?: FilterState;
  sortKey: SortKey;
}>();

const emit = defineEmits<{
  "apply-filter": [state: FilterState];
  "draft-filter-change": [state: FilterState];
  "reset-filter": [];
  "select-sort": [key: SortKey];
}>();

const filterPanelOpen = ref(false);
const sortMenuOpen = ref(false);
/** 筛选草稿：打开面板时从已应用状态复制，确定/重置才提交。 */
const filterDraft = ref<FilterState>(copyFilterState(props.draftFilterState || props.filterState));
const hasPendingFilterDraft = ref(
  JSON.stringify(props.draftFilterState || props.filterState) !== JSON.stringify(props.filterState),
);
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
  activeAnchor = null;
  activeGetPopover = null;
  if (resizeCleanup) {
    resizeCleanup();
    resizeCleanup = undefined;
  }
}

function copyFilterState(state: FilterState): FilterState {
  return {
    salary: [...state.salary],
    experience: [...state.experience],
    degree: [...state.degree],
    welfare: [...state.welfare],
  };
}

function syncFilterDraft() {
  if (!hasPendingFilterDraft.value) filterDraft.value = copyFilterState(props.filterState);
}

let scrollCleanup: (() => void) | undefined;
let resizeCleanup: (() => void) | undefined;
let activeAnchor: HTMLElement | null = null;
let activeGetPopover: (() => HTMLElement | null) | null = null;

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
  activeAnchor = anchor;
  activeGetPopover = getPopover;
  // 任意容器滚动时关闭浮层，避免 fixed 定位漂移
  if (scrollCleanup) scrollCleanup();
  const onScroll = () => { closePopovers(); };
  window.addEventListener("scroll", onScroll, { capture: true, passive: true });
  scrollCleanup = () => window.removeEventListener("scroll", onScroll, { capture: true });
  // 窗口缩放/布局变化时重新锚定当前浮层
  if (resizeCleanup) resizeCleanup();
  const onResize = () => {
    if (activeAnchor && activeGetPopover) {
      const popover = activeGetPopover();
      if (popover) positionPopover(activeAnchor, popover);
    }
  };
  window.addEventListener("resize", onResize);
  resizeCleanup = () => window.removeEventListener("resize", onResize);
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
  // 面板关闭不代表用户放弃草稿；下一次打开继续显示上次未确定的选择。
  syncFilterDraft();
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
  emit("apply-filter", copyFilterState(filterDraft.value));
  hasPendingFilterDraft.value = false;
  closePopovers();
}

function resetFilters() {
  hasPendingFilterDraft.value = false;
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

function onDraftFilterChange(state: FilterState) {
  filterDraft.value = copyFilterState(state);
  hasPendingFilterDraft.value = true;
  emit("draft-filter-change", copyFilterState(state));
}

watch(() => props.filterState, syncFilterDraft, { deep: true });
watch(() => props.draftFilterState, (draft) => {
  if (!draft) return;
  filterDraft.value = copyFilterState(draft);
  hasPendingFilterDraft.value = JSON.stringify(draft) !== JSON.stringify(props.filterState);
}, { deep: true });

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
        @update:model-value="onDraftFilterChange"
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
