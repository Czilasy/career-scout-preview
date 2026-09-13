import { mount } from "@vue/test-utils";
import JobWorkspace from "../JobWorkspace.vue";
import type { JobItem, SceneIdentity } from "../../types";
import { useDiscoverySceneState } from "../../composables/useDiscoverySceneState";

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    job_id: "j1",
    title: "Python 后端",
    company: "测试公司",
    salary: "20-40K",
    location: "上海 · 浦东新区",
    ...overrides,
  };
}

describe("JobWorkspace job count", () => {

  it("shows job count without location summary", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ location: "上海" })],
        emptyMessage: "暂无岗位",
      },
    });
    expect(wrapper.find('[data-testid="location-summary"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("1 个岗位");
  });

  it("removes empty location dots from job cards and detail", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ location: "东莞··" })],
        emptyMessage: "暂无岗位",
      },
    });
    const rows = wrapper.findAll('[data-testid="job-row"]');
    expect(rows[0].text()).toContain("东莞");
    expect(rows[0].text()).not.toContain("··");
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("东莞");
    expect(wrapper.get('[data-testid="job-detail"]').text()).not.toContain("··");
  });
});

describe("JobWorkspace company insight buttons (B058/B065)", () => {
  it("shows insight buttons at the far right of the job facts row and keeps content in hover popovers", async () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [
          job({
            verdict: "match",
            verdict_reason: "技能匹配",
            caveats: ["优先英语六级，候选人未提供"],
          }),
        ],
        emptyMessage: "暂无岗位",
      },
    });
    const line = wrapper.get(".job-detail-facts");
    expect(line.findAll(".company-insight-button").map((button) => button.text())).toEqual([
      "AI 判断说明", "软性要求提醒",
    ]);
    expect(line.get(".ai-insight").attributes("data-platform")).toBe("boss");
    expect(wrapper.get(".jd-content").attributes("data-platform")).toBe("boss");
    expect(line.findAll(".company-insight-popover")).toHaveLength(2);
    expect(wrapper.find(".verdict-pair").exists()).toBe(false);
    await line.findAll(".company-insight")[0].trigger("mouseenter");
    expect(line.findAll(".company-insight-popover")[0].text()).toContain("技能匹配");
    expect(line.findAll(".ai-insight-list li")).toHaveLength(1);
    const softItems = line.findAll(".company-insight-popover")[1].findAll(".soft-insight-list li");
    expect(softItems.map((item) => item.text())).toEqual(["优先英语六级，候选人未提供"]);
  });

  it("binds the description divider to the selected job platform", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ platform: "zhilian", verdict: "match", verdict_reason: "技能匹配" })],
        emptyMessage: "暂无岗位",
      },
    });

    expect(wrapper.get(".jd-content").attributes("data-platform")).toBe("zhilian");
  });

  it("hides soft requirements for a not-matched job", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ verdict: "not_match", verdict_reason: "薪资不符", caveats: ["仅供参考"] })],
        emptyMessage: "暂无岗位",
      },
    });
    const line = wrapper.get(".job-detail-facts");
    expect(line.findAll(".company-insight-button").map((button) => button.text())).toEqual(["AI 判断说明"]);
    expect(line.text()).toContain("AI 判断说明");
    expect(line.text()).not.toContain("软性要求提醒");
  });

  it("does not show soft requirements when all reminder content is blank", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({
          verdict: "match",
          verdict_reason: "技能匹配",
          caveats: ["  "],
          flags: [{ code: "M1", level: "medium", reason: "" }],
        })],
        emptyMessage: "暂无岗位",
      },
    });

    expect(wrapper.get(".job-detail-facts").findAll(".company-insight-button").map((button) => button.text())).toEqual(["AI 判断说明"]);
  });
});

describe("JobWorkspace infinite scroll sentinel", () => {
  // 批四 T071：把 setup.ts 里的 IntersectionObserver 假件真正接线——哨兵
  // 进入视口自动展开下一批是用户可见行为，此前没有任何用例触发过该假件。
  function manyJobs(count: number): JobItem[] {
    return Array.from({ length: count }, (_, index) => job({ job_id: `j${index + 1}` }));
  }

  function lastObserver(): { trigger: (isIntersecting: boolean) => void } {
    const observers = (globalThis as unknown as {
      __mockIntersectionObservers: Array<{ trigger: (isIntersecting: boolean) => void }>;
    }).__mockIntersectionObservers;
    expect(observers.length).toBeGreaterThan(0);
    return observers.at(-1)!;
  }

  it("loads the next batch only when the sentinel enters the viewport", async () => {
    const wrapper = mount(JobWorkspace, {
      props: { jobs: manyJobs(5), batchSize: 2, emptyMessage: "暂无岗位" },
    });
    const rows = () => wrapper.findAll('[data-testid="job-row"]');
    expect(rows()).toHaveLength(2);

    lastObserver().trigger(false);
    await wrapper.vm.$nextTick();
    expect(rows()).toHaveLength(2);

    lastObserver().trigger(true);
    await wrapper.vm.$nextTick();
    expect(rows()).toHaveLength(4);

    lastObserver().trigger(true);
    await wrapper.vm.$nextTick();
    expect(rows()).toHaveLength(5); // 展示数封顶在岗位总数，不越界渲染
  });
});

describe("JobWorkspace detail scroll boundary", () => {
  it("keeps the detail shell fixed and places only the JD in its scroll window", () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ jd: "职责一\n职责二" })],
        emptyMessage: "暂无岗位",
      },
    });

    const detail = wrapper.get('[data-testid="job-detail"]');
    const jdScroll = detail.get('[data-testid="job-detail-jd-scroll"]');

    expect(detail.get(".job-detail-fixed").text()).toContain("测试公司");
    expect(jdScroll.text()).toContain("职责一");
    expect(jdScroll.find(".job-detail-header").exists()).toBe(false);
    expect(jdScroll.find(".job-detail-actions").exists()).toBe(false);
    expect(detail.get(".job-detail-header").element.parentElement).toBe(detail.element);
    expect(detail.get(".job-detail-actions").element.parentElement).toBe(detail.element);
  });
});

describe("JobWorkspace scene handoff", () => {
  it("does not overwrite the next identity while jobs and identity change together", async () => {
    sessionStorage.clear();
    const sceneStore = useDiscoverySceneState();
    const first: SceneIdentity = { profileId: "profile-a", runEpoch: "run-a", platform: "boss" };
    const next: SceneIdentity = { profileId: "profile-a", runEpoch: "run-b", platform: "boss" };
    sceneStore.saveCurrent(next, {
      sortKey: "salary_desc",
      selectedJobKey: "boss:job-b",
      visibleCount: 60,
    });

    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ job_id: "job-a", platform: "boss", platform_job_id: "job-a" })],
        emptyMessage: "暂无岗位",
        sceneIdentity: first,
      },
    });

    await wrapper.setProps({
      jobs: [job({ job_id: "job-b", platform: "boss", platform_job_id: "job-b" })],
      sceneIdentity: next,
    });

    const restored = sceneStore.getCurrent(next);
    expect(restored.sortKey).toBe("salary_desc");
    expect(restored.selectedJobKey).toBe("boss:job-b");
    expect(restored.visibleCount).toBe(60);

    await wrapper.setProps({
      jobs: [job({ job_id: "job-b", platform: "boss", platform_job_id: "job-b", company: "更新后的公司" })],
    });
    expect(sceneStore.getCurrent(next).visibleCount).toBe(60);
    await wrapper.unmount();
  });

  it("re-applies the list scroll position when the list mounts after the restore tick", async () => {
    sessionStorage.clear();
    const sceneStore = useDiscoverySceneState();
    const previous: SceneIdentity = { profileId: "profile-scroll-a", runEpoch: "run-scroll-a", platform: "boss" };
    const next: SceneIdentity = { profileId: "profile-scroll-b", runEpoch: "run-scroll-b", platform: "boss" };
    sceneStore.saveCurrent(next, { listScrollTop: 321 });

    const wrapper = mount(JobWorkspace, {
      props: { jobs: [], emptyMessage: "暂无岗位", sceneIdentity: previous },
    });

    // 切身份瞬间结果还没到：列表不存在，恢复那一拍拿不到滚动元素。
    await wrapper.setProps({ sceneIdentity: next });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".job-list").exists()).toBe(false);

    // 结果到达、列表出现：补应用该身份保存的滚动位置（现场不丢）。
    await wrapper.setProps({
      jobs: [job({ job_id: "later", platform: "boss", platform_job_id: "later" })],
    });
    await wrapper.vm.$nextTick();
    expect((wrapper.get(".job-list").element as HTMLElement).scrollTop).toBe(321);
    await wrapper.unmount();
  });

  it("restores the saved selected job and JD scroll when the result list arrives late", async () => {
    sessionStorage.clear();
    const sceneStore = useDiscoverySceneState();
    const identity: SceneIdentity = {
      profileId: "profile-late-select",
      runEpoch: "run-late-select",
      platform: "zhilian",
    };
    // 刷新恢复：结果数据比现场晚到，选中键在恢复那一刻还无法在列表里确认。
    sceneStore.saveCurrent(identity, {
      selectedJobKey: "zhilian:job-3",
      userSelectedDetail: true,
      detailOpen: true,
      jdScrollTop: 212,
      visibleCount: 30,
    });

    const wrapper = mount(JobWorkspace, {
      props: { jobs: [], emptyMessage: "暂无岗位", sceneIdentity: identity },
    });
    await wrapper.vm.$nextTick();

    await wrapper.setProps({
      jobs: [
        job({ job_id: "1", platform_job_id: "job-1", platform: "zhilian", title: "岗位一" }),
        job({ job_id: "2", platform_job_id: "job-2", platform: "zhilian", title: "岗位二" }),
        job({ job_id: "3", platform_job_id: "job-3", platform: "zhilian", title: "岗位三", jd: "第三条 JD" }),
      ],
    });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    // 选中接回存档里的第 3 条（不是列表第一条），JD 阅读位置一并接回。
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("岗位三");
    expect(wrapper.get('[data-testid="job-detail-jd-scroll"]').element.scrollTop).toBe(212);
    await wrapper.unmount();
  });

  it("keeps an unconfirmed list filter draft after closing the popover and remounting", async () => {
    sessionStorage.clear();
    const identity: SceneIdentity = {
      profileId: "profile-filter-draft",
      runEpoch: "run-filter-draft",
      platform: "boss",
    };
    const props = {
      jobs: [job({ platform: "boss", platform_job_id: "job-filter-draft" })],
      emptyMessage: "暂无岗位",
      sceneIdentity: identity,
    };
    const wrapper = mount(JobWorkspace, { props });

    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    const salaryOption = wrapper
      .findAll('[data-testid="filter-option"]')
      .find((button) => button.text() === "5-10K");
    expect(salaryOption).toBeDefined();
    await salaryOption!.trigger("click");
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    expect(
      wrapper.findAll('[data-testid="filter-option"]').find((button) => button.text() === "5-10K")
        ?.attributes("aria-pressed"),
    ).toBe("true");

    await wrapper.unmount();
    const remounted = mount(JobWorkspace, { props });
    await remounted.get('[data-testid="result-filter-toggle"]').trigger("click");
    expect(
      remounted.findAll('[data-testid="filter-option"]').find((button) => button.text() === "5-10K")
        ?.attributes("aria-pressed"),
    ).toBe("true");
    await remounted.unmount();
  });

  it("does not apply an unconfirmed list filter draft after remounting", async () => {
    sessionStorage.clear();
    const identity: SceneIdentity = {
      profileId: "profile-filter-not-applied",
      runEpoch: "run-filter-not-applied",
      platform: "boss",
    };
    const props = {
      jobs: [
        job({ job_id: "low", platform: "boss", platform_job_id: "low", salary: "5-10K" }),
        job({ job_id: "high", platform: "boss", platform_job_id: "high", salary: "20-40K" }),
      ],
      emptyMessage: "暂无岗位",
      sceneIdentity: identity,
    };
    const wrapper = mount(JobWorkspace, { props });
    await wrapper.get('[data-testid="result-filter-toggle"]').trigger("click");
    const salaryOption = wrapper.findAll('[data-testid="filter-option"]')
      .find((button) => button.text() === "5-10K");
    await salaryOption!.trigger("click");
    expect(wrapper.findAll('[data-testid="job-row"]')).toHaveLength(2);
    await wrapper.unmount();

    const remounted = mount(JobWorkspace, { props });
    expect(remounted.findAll('[data-testid="job-row"]')).toHaveLength(2);
    await remounted.unmount();
  });

  it("keeps a manually closed detail closed when recrawl changes the job collection", async () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [job({ job_id: "one", platform: "boss", platform_job_id: "one" })],
        emptyMessage: "暂无岗位",
        sceneIdentity: { profileId: "profile-detail", runEpoch: "run-detail", platform: "boss" },
      },
    });
    await wrapper.get('[aria-label="关闭岗位详情"]').trigger("click");
    expect(wrapper.find('[data-testid="job-detail"]').exists()).toBe(false);

    await wrapper.setProps({
      jobs: [
        job({ job_id: "one", platform: "boss", platform_job_id: "one" }),
        job({ job_id: "two", platform: "boss", platform_job_id: "two" }),
      ],
    });
    expect(wrapper.find('[data-testid="job-detail"]').exists()).toBe(false);
    await wrapper.unmount();
  });

  it("reports when a refreshed result removes the manually selected job", async () => {
    const wrapper = mount(JobWorkspace, {
      props: {
        jobs: [
          job({ job_id: "one", platform: "boss", platform_job_id: "one" }),
          job({ job_id: "two", platform: "boss", platform_job_id: "two" }),
        ],
        emptyMessage: "暂无岗位",
        sceneIdentity: { profileId: "profile-fallback", runEpoch: "run-fallback", platform: "boss" },
      },
    });
    await wrapper.findAll('[data-testid="job-row"]')[1].trigger("click");

    await wrapper.setProps({
      jobs: [job({ job_id: "one", platform: "boss", platform_job_id: "one" })],
    });

    expect(wrapper.emitted("selection-fallback")).toHaveLength(1);
  });
});
