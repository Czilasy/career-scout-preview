<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Activity, Bell, Bot, Moon, Star, Sun, UserRound, X } from "@lucide/vue";
import AiSettingsDialog from "./components/AiSettingsDialog.vue";
import BrowserAccountsDialog from "./components/BrowserAccountsDialog.vue";
import EnvCheckDialog from "./components/EnvCheckDialog.vue";
import NoticeBar from "./components/NoticeBar.vue";
import ReminderDrawer from "./components/ReminderDrawer.vue";
import DiscoveryView from "./views/DiscoveryView.vue";
import { apiRequest, errorMessage, initializeSession } from "./api";
import { getJobReminderCount } from "./jobFeedback";
import { useTheme } from "./composables/useTheme";
import type { CandidateProfile, Notice } from "./types";

// 主题切换：mode（明暗）由顶栏按钮触发；platform（boss/智联）由 DiscoveryView 同步。
const { mode, toggleTheme } = useTheme();

const themeToggleLabel = computed(() =>
  mode.value === "light" ? "切换到暗色模式" : "切换到浅色模式");

// ---------------------------------------------------------------------------
// 顶栏本轮状态胶囊：纯展示。数据来自 DiscoveryView 上抛的 round-status，
// 空闲（无任务上下文且无结果）时不渲染。
// ---------------------------------------------------------------------------
const roundStatus = ref<{ platform: "boss" | "zhilian"; phase: string; judged: number } | null>(null);
const roundStatusRunning = computed(() =>
  roundStatus.value?.phase === "scraping" || roundStatus.value?.phase === "screening");
const roundStatusText = computed(() => {
  if (!roundStatus.value) return "";
  if (roundStatus.value.phase === "scraping") return "抓取进行中";
  if (roundStatus.value.phase === "screening") return "筛选进行中";
  return `${roundStatus.value.judged} 个岗位已判定`;
});

const aiSettingsOpen = ref(false);
const browserAccountsOpen = ref(false);
const envCheckOpen = ref(false);
const profiles = ref<CandidateProfile[]>([]);
const currentProfileId = ref("");
const notice = ref<Notice | null>(null);
let noticeTimer: number | undefined;

const favoritesOpen = ref(false);
const favorites = ref<Record<string, unknown>[]>([]);
const favPanelEl = ref<HTMLElement | null>(null);
const favTriggerEl = ref<HTMLButtonElement | null>(null);

async function loadFavorites() {
  try {
    const data = await apiRequest<{ items?: Record<string, unknown>[] }>("/api/favorites");
    favorites.value = data.items || [];
  } catch {
    favorites.value = [];
  }
}

function toggleFavorites() {
  if (favoritesOpen.value) {
    favoritesOpen.value = false;
  } else {
    favoritesOpen.value = true;
    // 两个抽屉互斥：打开收藏时收起提醒，避免叠加导致点空白“回落到收藏”。
    reminderDrawerOpen.value = false;
    void loadFavorites();
  }
}

// 点击收藏面板与触发按钮之外的任意区域自动收起（与提醒抽屉的点外部关闭一致）。
function onDocPointerDown(event: PointerEvent) {
  if (!favoritesOpen.value) return;
  const target = event.target as Node | null;
  if (!target) return;
  if (favPanelEl.value && favPanelEl.value.contains(target)) return;
  if (favTriggerEl.value && favTriggerEl.value.contains(target)) return;
  favoritesOpen.value = false;
}

// 抽屉打开时聚焦面板并挂全局指针监听；关闭时移除监听（Esc 仍可关闭）。
watch(favoritesOpen, (open) => {
  if (open) {
    nextTick(() => favPanelEl.value?.focus());
    document.addEventListener("pointerdown", onDocPointerDown);
  } else {
    document.removeEventListener("pointerdown", onDocPointerDown);
  }
});

function handleFavoritesKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") favoritesOpen.value = false;
}

async function removeFavorite(job: Record<string, unknown>) {
  const profileId = String(job.profile_id || "");
  const jobId = String(job.job_id || "");
  if (!profileId || !jobId) return;
  try {
    await apiRequest("/api/pipeline/jobs/interest/cancel", {
      method: "POST",
      json: {
        profile_id: profileId,
        job: {
          job_id: jobId,
          job_link: job.job_link,
          title: job.title,
          company: job.company,
          salary: job.salary,
          location: job.location,
        },
      },
    });
    favorites.value = favorites.value.filter((j) => j.job_id !== job.job_id);
    showNotice({ message: "已取消收藏", tone: "info" });
  } catch (error) {
    showNotice({ message: errorMessage(error, "取消收藏失败"), tone: "error" });
  }
}

onMounted(async () => {
  try {
    await initializeSession();
    const profileData = await apiRequest<{ profiles?: CandidateProfile[] }>("/api/profiles");
    profiles.value = profileData.profiles || [];
    const saved = localStorage.getItem("career-scout-current-profile") || "";
    currentProfileId.value = profiles.value.some((profile) => profile.id === saved)
      ? saved
      : profiles.value[0]?.id || "";
    // 顶栏收藏徽标随首屏加载展示，不必等用户先打开过面板。
    void loadFavorites();
  } catch (error) {
    showNotice({ message: errorMessage(error, "WebUI 初始化失败"), tone: "error" });
  }
});

onBeforeUnmount(() => {
  if (noticeTimer) window.clearTimeout(noticeTimer);
  document.removeEventListener("pointerdown", onDocPointerDown);
});

function selectProfile(profileId: string) {
  currentProfileId.value = profileId;
  if (profileId) localStorage.setItem("career-scout-current-profile", profileId);
}

function showNotice(next: Notice) {
  if (noticeTimer) window.clearTimeout(noticeTimer);
  notice.value = next;
  const delay = next.tone === "error" ? 8000 : next.tone === "warning" ? 5000 : 3000;
  noticeTimer = window.setTimeout(() => {
    notice.value = null;
    noticeTimer = undefined;
  }, delay);
}

function dismissNotice() {
  if (noticeTimer) window.clearTimeout(noticeTimer);
  noticeTimer = undefined;
  notice.value = null;
}

function acceptCreatedProfile(profile: CandidateProfile) {
  const index = profiles.value.findIndex((item) => item.id === profile.id);
  if (index >= 0) profiles.value[index] = profile;
  else profiles.value.push(profile);
  selectProfile(profile.id);
}

// ---------------------------------------------------------------------------
// 提醒入口（Task 009）：App 持有当前 profile 的提醒状态。
// count/list/state 全部以服务端响应为唯一来源；不在前端计算 720h 资格，
// 不传 platform 过滤，不做乐观更新。
// ---------------------------------------------------------------------------

const reminderDrawerOpen = ref(false);
const reminderTotal = ref(0);
/** 请求序号：profile 切换后，旧 profile 的 count 响应不得覆盖新 profile。 */
let reminderCountSeq = 0;

async function refreshReminderCount() {
  const profileId = currentProfileId.value;
  if (!profileId) {
    reminderTotal.value = 0;
    return;
  }
  const seq = ++reminderCountSeq;
  try {
    const data = await getJobReminderCount(profileId);
    // 丢弃旧 profile 的陈旧响应；徽标只采用当前 profile 的服务端总数。
    if (seq !== reminderCountSeq || profileId !== currentProfileId.value) return;
    reminderTotal.value = data.total;
  } catch {
    // 加载失败：保留上次已知数量，不清零也不伪造（下次变更时再刷新）。
  }
}

const reminderBadgeText = computed(() =>
  reminderTotal.value >= 100 ? "99+" : String(reminderTotal.value));

// 可访问名称始终包含真实总数；100+ 时视觉显示 99+。
const reminderAriaLabel = computed(() =>
  reminderTotal.value > 0
    ? `查看投递提醒，共 ${reminderTotal.value} 个逾期岗位`
    : "查看投递提醒");

function toggleReminderDrawer() {
  if (reminderDrawerOpen.value) {
    reminderDrawerOpen.value = false;
    return;
  }
  // profile 为空时不请求也不打开抽屉。
  if (!currentProfileId.value) return;
  reminderDrawerOpen.value = true;
  // 两个抽屉互斥：打开提醒时收起收藏。
  favoritesOpen.value = false;
}

// 抽屉触发按钮：鼠标左键“按下”即展开/收起，不等松开；
// 键盘 Enter/Space 触发的是 detail===0 的 click，照常响应（可访问性）；
// 鼠标产生的 click（detail>=1）跳过，避免与 mousedown 双重触发。
function handleDrawerTrigger(trigger: () => void, event: MouseEvent) {
  if (event.type === "mousedown") {
    if (event.button === 0) trigger();
    return;
  }
  if (event.detail === 0) trigger();
}

// profile 初始化/切换：关闭并重置旧抽屉，废弃在途旧请求，重新加载 count。
watch(currentProfileId, (profileId) => {
  reminderDrawerOpen.value = false;
  reminderTotal.value = 0;
  reminderCountSeq += 1;
  if (profileId) void refreshReminderCount();
});

// 详情动作（DiscoveryView）或抽屉快捷动作（ReminderDrawer）成功后刷新 count。
// 抽屉列表由 ReminderDrawer 自身按服务端刷新结果更新。
function handleJobFeedbackChanged(payload?: { profileId?: string }) {
  // profile 已切换时丢弃旧 action 触发的刷新，不代旧 profile 发请求。
  if (payload?.profileId && payload.profileId !== currentProfileId.value) return;
  void refreshReminderCount();
  // 收藏/取消收藏属于 interest 变更，同步刷新收藏数量。
  void loadFavorites();
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <a class="brand" href="/" aria-label="Career Scout 工作台首页">
        <svg class="brand-mark" width="30" height="30" viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <path d="M25.5 6.5 L18.3 18.3 L6.5 25.5 Z" fill="var(--logo-a)" />
          <path d="M25.5 6.5 L13.7 13.7 L6.5 25.5 Z" fill="var(--logo-b)" />
          <circle cx="16" cy="16" r="1.9" fill="var(--logo-dot)" />
        </svg>
        <span class="brand-name">Career<span class="tick">·</span>Scout</span>
      </a>

      <div v-if="roundStatus" class="round-pill" data-testid="round-status-pill">
        <span v-if="roundStatusRunning" class="live" aria-hidden="true"></span>
        <span class="pf">{{ roundStatus.platform === 'boss' ? 'BOSS' : '智联' }}</span>
        <span class="sep" aria-hidden="true"></span>
        <span class="round-status-text">{{ roundStatusText }}</span>
      </div>
      <span v-else aria-hidden="true"></span>

      <div class="header-actions">
        <button
          class="button secondary reminder-trigger"
          type="button"
          data-testid="reminder-trigger"
          :aria-label="reminderAriaLabel"
          :disabled="!currentProfileId"
          @mousedown="handleDrawerTrigger(toggleReminderDrawer, $event)"
          @click="handleDrawerTrigger(toggleReminderDrawer, $event)"
        >
          <Bell :size="18" aria-hidden="true" /><span>提醒</span>
          <em
            v-if="reminderTotal > 0"
            class="fav-badge reminder-badge"
            data-testid="reminder-badge"
            aria-hidden="true"
          >{{ reminderBadgeText }}</em>
        </button>
        <button
          ref="favTriggerEl"
          class="button secondary favorites-trigger"
          type="button"
          aria-label="查看收藏"
          @mousedown="handleDrawerTrigger(toggleFavorites, $event)"
          @click="handleDrawerTrigger(toggleFavorites, $event)"
        >
          <Star :size="18" aria-hidden="true" /><span>收藏</span>
          <em v-if="favorites.length" class="fav-badge">{{ favorites.length }}</em>
        </button>
        <button
          class="button secondary browser-accounts-trigger"
          type="button"
          data-testid="browser-accounts-trigger"
          aria-label="管理自动化浏览器账号"
          @click="browserAccountsOpen = true"
        >
          <UserRound :size="18" aria-hidden="true" /><span>浏览器账号</span>
        </button>
        <button
          class="button secondary env-check-trigger"
          type="button"
          data-testid="env-check-trigger"
          aria-label="环境检查"
          @click="envCheckOpen = true"
        >
          <Activity :size="18" aria-hidden="true" /><span>环境检查</span>
        </button>
        <button
          class="button secondary ai-settings-trigger"
          type="button"
          data-testid="ai-settings-trigger"
          aria-label="打开 AI 设置"
          @click="aiSettingsOpen = true"
        >
          <Bot :size="18" aria-hidden="true" /><span>AI 设置</span>
        </button>
        <button
          class="icon-button theme-toggle"
          type="button"
          data-testid="theme-toggle"
          :aria-label="themeToggleLabel"
          :title="themeToggleLabel"
          @click="toggleTheme()"
        >
          <Sun v-if="mode === 'dark'" :size="18" aria-hidden="true" />
          <Moon v-else :size="18" aria-hidden="true" />
        </button>
      </div>
    </header>

    <aside
      v-if="favoritesOpen"
      ref="favPanelEl"
      class="fav-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="我的收藏"
      tabindex="-1"
      @keydown="handleFavoritesKeydown"
    >
      <header class="fav-drawer-head">
        <div class="fav-drawer-heading">
          <h2>我的收藏</h2>
          <p v-if="favorites.length" class="fav-total">共 {{ favorites.length }} 个岗位</p>
        </div>
        <button type="button" class="icon-button" aria-label="关闭收藏面板" @click="favoritesOpen = false">
          <X :size="20" aria-hidden="true" />
        </button>
      </header>
      <div class="fav-drawer-body">
        <p v-if="!favorites.length" class="fav-drawer-empty">暂无收藏，在结果页点「收藏」即可添加。</p>
        <div v-else class="fav-drawer-list">
          <div
            v-for="job in favorites"
            :key="String(job.job_id || job.id)"
            class="fav-card"
          >
            <a
              class="fav-card-main"
              :href="String(job.job_link || '#')"
              target="_blank"
              rel="noopener"
            >
              <strong class="fav-card-title">{{ job.title || "未知岗位" }}</strong>
              <span class="fav-card-meta">{{ job.salary || "薪资面议" }} · {{ job.location || "" }}</span>
              <span class="fav-card-company">{{ job.company || "" }}</span>
            </a>
            <button
              type="button"
              class="fav-card-remove"
              aria-label="取消收藏"
              @click.stop.prevent="removeFavorite(job)"
            >
              <X :size="16" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </aside>

    <NoticeBar :notice="notice" @dismiss="dismissNotice" />

    <ReminderDrawer
      :open="reminderDrawerOpen"
      :profile-id="currentProfileId || null"
      @close="reminderDrawerOpen = false"
      @job-feedback-changed="handleJobFeedbackChanged"
    />

    <div class="app-content">
      <DiscoveryView
        :profile-id="currentProfileId"
        @notify="showNotice"
        @profile-created="acceptCreatedProfile"
        @job-feedback-changed="handleJobFeedbackChanged"
        @round-status="roundStatus = $event"
        @open-browser-accounts="browserAccountsOpen = true"
      />
    </div>

    <AiSettingsDialog
      :open="aiSettingsOpen"
      @close="aiSettingsOpen = false"
    />
    <BrowserAccountsDialog
      :open="browserAccountsOpen"
      @close="browserAccountsOpen = false"
    />
    <EnvCheckDialog
      :open="envCheckOpen"
      @close="envCheckOpen = false"
      @open-browser-accounts="browserAccountsOpen = true; envCheckOpen = false"
      @open-ai-settings="aiSettingsOpen = true; envCheckOpen = false"
    />
  </div>
</template>
