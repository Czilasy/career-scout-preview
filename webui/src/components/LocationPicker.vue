<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Check, MapPin } from "@lucide/vue";
import { apiRequest } from "../api";
import { locationSummary } from "../location";
import type {
  LocationCatalogEntry,
  LocationCatalogResponse,
  LocationCondition,
  Platform,
} from "../types";

const props = withDefaults(defineProps<{
  city: string;
  platform: Platform;
  modelValue: LocationCondition[];
  disabled?: boolean;
}>(), {
  disabled: false,
});

const emit = defineEmits<{
  (e: "update:modelValue", value: LocationCondition[]): void;
  (e: "remove"): void;
}>();

const rootEl = ref<HTMLElement | null>(null);
const open = ref(false);
const districts = ref<LocationCatalogEntry[]>([]);
const loading = ref(false);
const unavailable = ref(false);
const cityCode = ref("");
const panelStyle = ref<Record<string, string>>({});

const label = computed(() => locationSummary(props.modelValue) || props.city);
const selectedCount = computed(() => props.modelValue.length);
const selectedDistrictCodes = computed(() => new Set(
  props.modelValue.map((location) => location.district_code),
));
const selectedDistrictsWithChildren = computed(() =>
  districts.value.filter(
    (district) => selectedDistrictCodes.value.has(district.code) && (district.children || []).length,
  ),
);

function isDistrictSelected(district: LocationCatalogEntry): boolean {
  return selectedDistrictCodes.value.has(district.code);
}

function isBusinessSelected(district: LocationCatalogEntry, business: LocationCatalogEntry): boolean {
  return props.modelValue.some(
    (location) =>
      location.district_code === district.code && location.business_code === business.code,
  );
}

function positionPanel(): void {
  const el = rootEl.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const width = Math.min(380, window.innerWidth - 24);
  const maxHeight = Math.min(460, Math.floor(window.innerHeight * 0.6));
  let left = Math.max(12, rect.left);
  if (left + width > window.innerWidth - 12) {
    left = Math.max(12, window.innerWidth - width - 12);
  }
  let top = rect.bottom + 6;
  if (top + maxHeight > window.innerHeight - 12) {
    top = Math.max(12, rect.top - maxHeight - 6);
  }
  panelStyle.value = {
    position: "fixed",
    left: `${left}px`,
    top: `${top}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
    zIndex: "2000",
  };
}

async function loadCatalog(): Promise<void> {
  loading.value = true;
  unavailable.value = false;
  try {
    const data = await apiRequest<LocationCatalogResponse>(
      `/api/location-catalog?platform=${props.platform}&city=${encodeURIComponent(props.city)}`,
    );
    districts.value = data.districts || [];
    cityCode.value = data.city_code || "";
  } catch {
    districts.value = [];
    unavailable.value = true;
  } finally {
    loading.value = false;
  }
}

function toggleOpen(): void {
  if (props.disabled) return;
  open.value = !open.value;
  if (open.value) {
    if (!districts.value.length && !unavailable.value) void loadCatalog();
    void nextTick(positionPanel);
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    open.value = false;
    return;
  }
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  toggleOpen();
}

function handleDocumentPointerDown(event: PointerEvent): void {
  if (!open.value) return;
  const target = event.target as Node | null;
  if (rootEl.value && target && !rootEl.value.contains(target)) {
    open.value = false;
  }
}

let removeReposition: (() => void) | null = null;

function addRepositionListeners(): void {
  removeReposition?.();
  window.addEventListener("resize", positionPanel);
  window.addEventListener("scroll", positionPanel, true);
  removeReposition = () => {
    window.removeEventListener("resize", positionPanel);
    window.removeEventListener("scroll", positionPanel, true);
  };
}

function clearRepositionListeners(): void {
  removeReposition?.();
  removeReposition = null;
}

onMounted(() => {
  document.addEventListener("pointerdown", handleDocumentPointerDown, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", handleDocumentPointerDown, true);
  clearRepositionListeners();
});
watch(open, (value) => {
  if (value) {
    void nextTick(() => {
      positionPanel();
      addRepositionListeners();
    });
  } else {
    clearRepositionListeners();
  }
});

function toggleDistrict(district: LocationCatalogEntry): void {
  const current = props.modelValue.filter(
    (location) => location.district_code !== district.code,
  );
  if (!isDistrictSelected(district)) {
    const condition: LocationCondition = {
      platform: props.platform,
      city_name: props.city,
      city_code: cityCode.value,
      district_name: district.name,
      district_code: district.code,
    };
    condition.label = locationSummary([condition]);
    current.push(condition);
  }
  emit("update:modelValue", current);
}

function toggleBusiness(district: LocationCatalogEntry, business: LocationCatalogEntry): void {
  const current = props.modelValue.map((location) => {
    if (location.district_code !== district.code) return location;
    if (location.business_code === business.code) {
      return {
        ...location,
        business_name: undefined,
        business_code: undefined,
      };
    }
    return {
      ...location,
      business_name: business.name,
      business_code: business.code,
    };
  });
  emit("update:modelValue", current);
}

function clearLocations(): void {
  emit("update:modelValue", []);
}

function removeCity(): void {
  emit("remove");
}
</script>

<template>
  <span
    ref="rootEl"
    class="city-chip"
    :class="{ open, 'has-locations': modelValue.length }"
    data-testid="city-chip"
  >
    <span
      class="city-chip-main"
      role="button"
      tabindex="0"
      data-testid="city-chip-toggle"
      :aria-expanded="open"
      :aria-haspopup="true"
      :aria-label="`选择 ${city} 区/镇`"
      :class="{ disabled }"
      @click="toggleOpen"
      @keydown="handleKeydown"
    >{{ label }}</span>
    <button
      type="button"
      class="city-chip-remove"
      aria-label="删除城市"
      :disabled="disabled"
      @click="removeCity"
    >×</button>

    <div
      v-if="open"
      class="location-panel"
      :style="panelStyle"
      data-testid="location-panel"
      role="dialog"
      aria-label="选择区/镇"
    >
      <header class="location-panel-header">
        <div class="location-panel-heading">
          <strong>{{ city }} · 区/县（可多选）</strong>
          <span v-if="selectedCount" class="location-selected-count">已选 {{ selectedCount }} 项</span>
        </div>
        <button
          type="button"
          class="button secondary small location-panel-clear"
          data-testid="location-clear"
          :disabled="!selectedCount"
          @click="clearLocations"
        >清空地点</button>
      </header>

      <div class="location-panel-body">
        <div v-if="loading" class="location-panel-state" data-testid="location-loading">
          <MapPin :size="18" aria-hidden="true" />
          <span>正在加载区/镇…</span>
        </div>
        <div
          v-else-if="unavailable || !districts.length"
          class="location-panel-state"
          data-testid="location-empty"
        >
          <MapPin :size="18" aria-hidden="true" />
          <span>暂无区/镇数据，按城市级搜索</span>
        </div>
        <template v-else>
          <div class="location-district-grid">
            <button
              v-for="district in districts"
              :key="district.code"
              type="button"
              class="location-choice"
              :class="{ selected: isDistrictSelected(district) }"
              :aria-pressed="isDistrictSelected(district)"
              :data-testid="`location-district-${district.code}`"
              @click="toggleDistrict(district)"
            >
              <Check v-if="isDistrictSelected(district)" :size="14" class="location-choice-check" aria-hidden="true" />
              <span>{{ district.name }}</span>
            </button>
          </div>
          <template v-if="platform === 'boss'">
            <div
              v-for="district in selectedDistrictsWithChildren"
              :key="district.code"
              class="location-business-block"
            >
              <div class="location-panel-section-title">{{ district.name }} · 商圈/镇（可选）</div>
              <div class="location-business-grid">
                <button
                  v-for="business in district.children || []"
                  :key="business.code"
                  type="button"
                  class="location-choice compact"
                  :class="{ selected: isBusinessSelected(district, business) }"
                  :aria-pressed="isBusinessSelected(district, business)"
                  :data-testid="`location-business-${district.code}-${business.code}`"
                  @click="toggleBusiness(district, business)"
                >
                  <Check v-if="isBusinessSelected(district, business)" :size="13" class="location-choice-check" aria-hidden="true" />
                  <span>{{ business.name }}</span>
                </button>
              </div>
            </div>
          </template>
        </template>
      </div>

    </div>
  </span>
</template>

<style scoped>
.city-chip {
  position: relative;
}

.city-chip-main {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 0 8px;
  cursor: pointer;
  overflow-wrap: anywhere;
}

.city-chip-main.disabled {
  cursor: default;
}

.location-panel {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--hair);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.22), 0 2px 8px rgba(0, 0, 0, 0.08);
}

.location-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex: none;
  padding: 12px 14px;
  border-bottom: 1px solid var(--hair-2);
}

.location-panel-heading {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
  min-width: 0;
}

.location-panel-heading strong {
  font-size: 0.95rem;
  color: var(--ink-1);
}

.location-selected-count {
  color: var(--text-soft);
  font-size: 0.78rem;
}

.location-panel-clear {
  flex: none;
  min-height: 30px;
  padding: 4px 10px;
}

.location-panel-body {
  flex: 1;
  min-height: 0;
  padding: 12px 14px;
  overflow-y: auto;
}

.location-panel-section-title {
  margin: 0 0 8px;
  color: var(--text-soft);
  font-size: 0.82rem;
  font-weight: 600;
}

.location-panel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 110px;
  color: var(--text-soft);
  font-size: 0.85rem;
  text-align: center;
}

.location-district-grid,
.location-business-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.location-business-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.location-choice {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 0;
  min-height: 38px;
  padding: 4px 8px;
  border: 1px solid var(--hair);
  border-radius: 6px;
  background: var(--panel-2);
  color: var(--ink-2);
  font-size: 0.84rem;
  line-height: 1.25;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}

.location-choice:hover {
  border-color: var(--brand-edge);
}

.location-choice.selected {
  border-color: var(--brand);
  background: var(--brand-wash);
  color: var(--brand-ink);
  font-weight: 600;
}

.location-choice-check {
  flex: none;
}

.location-choice.compact {
  min-height: 34px;
  font-size: 0.8rem;
}

.location-business-block {
  margin-bottom: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--hair-2);
}

</style>
