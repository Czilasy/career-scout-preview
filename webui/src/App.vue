<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Bell, Bot, BriefcaseBusiness, Star, UserRound, X } from "@lucide/vue";
import AiSettingsDialog from "./components/AiSettingsDialog.vue";
import BrowserAccountsDialog from "./components/BrowserAccountsDialog.vue";
import NoticeBar from "./components/NoticeBar.vue";
import ReminderDrawer from "./components/ReminderDrawer.vue";
import DiscoveryView from "./views/DiscoveryView.vue";
import { apiRequest, errorMessage, initializeSession } from "./api";
import { getJobReminderCount } from "./jobFeedback";
import type { CandidateProfile, Notice } from "./types";

const aiSettingsOpen = ref(false);
const browserAccountsOpen = ref(false);
const profiles = ref<CandidateProfile[]>([]);
const currentProfileId = ref("");
const notice = ref<Notice | null>(null);
let noticeTimer: number | undefined;

const favoritesOpen = ref(false);
const favorites = ref<Record<string, unknown>[]>([]);

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
    void loadFavorites();
  }
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
  } catch (error) {
    showNotice({ message: errorMessage(error, "WebUI 初始化失败"), tone: "error" });
  }
});

onBeforeUnmount(() => {
  if (noticeTimer) window.clearTimeout(noticeTimer);
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
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <a class="brand" href="/" aria-label="Career Scout 工作台首页">
        <span class="brand-mark" aria-hidden="true"><BriefcaseBusiness :size="20" /></span>
        <span><strong>Career Scout</strong><small>BOSS 求职工作台</small></span>
      </a>


      <div class="header-actions">
        <button
          class="button secondary reminder-trigger"
          type="button"
          data-testid="reminder-trigger"
          :aria-label="reminderAriaLabel"
          :disabled="!currentProfileId"
          @click="toggleReminderDrawer"
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
          class="button secondary favorites-trigger"
          type="button"
          aria-label="查看收藏"
          @click="toggleFavorites"
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
          class="button secondary ai-settings-trigger"
          type="button"
          data-testid="ai-settings-trigger"
          aria-label="打开 AI 设置"
          @click="aiSettingsOpen = true"
        >
          <Bot :size="18" aria-hidden="true" /><span>AI 设置</span>
        </button>
      </div>
    </header>

    <aside v-if="favoritesOpen" class="fav-drawer">
      <div class="fav-drawer-head">
        <strong>我的收藏</strong>
        <button type="button" class="fav-drawer-close" aria-label="关闭收藏面板" @click="favoritesOpen = false">&times;</button>
      </div>
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
  </div>
</template>
