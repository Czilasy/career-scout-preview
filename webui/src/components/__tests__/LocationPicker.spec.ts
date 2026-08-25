import { flushPromises, mount } from "@vue/test-utils";
import LocationPicker from "../LocationPicker.vue";
import type { LocationCondition } from "../../types";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const BOSS_CATALOG = {
  ok: true,
  platform: "boss",
  city: "上海",
  city_code: "101020100",
  districts: [
    {
      code: "310115",
      name: "浦东新区",
      children: [
        { code: "154", name: "北蔡" },
        { code: "39", name: "张江" },
      ],
    },
    { code: "310104", name: "徐汇区", children: [] },
  ],
};

function fetchMockFor(urlPart: string, payload: unknown) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/session")) return response({ token: "test" });
    if (url.includes(urlPart)) return response(payload);
    return response({});
  });
}

async function mountPicker(platform: "boss" | "zhilian" = "boss", modelValue: LocationCondition[] = []) {
  const wrapper = mount(LocationPicker, {
    props: { city: "上海", platform, modelValue, disabled: false },
  });
  await wrapper.get('[data-testid="city-chip-toggle"]').trigger("click");
  await flushPromises();
  return wrapper;
}

describe("LocationPicker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens a district panel and emits a selected district", async () => {
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker();
    expect(wrapper.find('[data-testid="location-panel"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="city-chip-toggle"]').attributes("aria-expanded")).toBe("true");
    expect(wrapper.find('[data-testid="location-panel"]').attributes("style")).toContain("position: fixed");
    expect(wrapper.get('[data-testid="location-panel"]').text()).toContain("上海 · 区/县（可多选）");
    expect(wrapper.find('[data-testid="location-close"]').exists()).toBe(false);
    expect(wrapper.findAll(".location-panel-section-title")).toHaveLength(0);
    expect(wrapper.get(".location-panel-header").find('[data-testid="location-clear"]').exists()).toBe(true);
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")![0][0]).toMatchObject([
      { district_name: "浦东新区", district_code: "310115" },
    ]);
  });

  it("supports multiple districts and clear", async () => {
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker();
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    const first = wrapper.emitted("update:modelValue")!.at(-1)![0] as LocationCondition[];
    await wrapper.setProps({ modelValue: first });
    await wrapper.get('[data-testid="location-district-310104"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")!.at(-1)![0]).toHaveLength(2);
    await wrapper.get('[data-testid="location-clear"]').trigger("click");
    expect(wrapper.emitted("update:modelValue")!.at(-1)![0]).toEqual([]);
  });

  it("shows business level only for BOSS", async () => {
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker("boss");
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    const selected = wrapper.emitted("update:modelValue")!.at(-1)![0] as LocationCondition[];
    await wrapper.setProps({ modelValue: selected });
    const business = wrapper.find('[data-testid="location-business-310115-154"]');
    expect(business.exists()).toBe(true);
    await business.trigger("click");
    const emitted = wrapper.emitted("update:modelValue")!.at(-1)![0] as LocationCondition[];
    expect(emitted[0].business_name).toBe("北蔡");
    expect(emitted[0].business_code).toBe("154");
  });

  it("shows city-level fallback when no district data exists", async () => {
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", {
      ok: true,
      platform: "boss",
      city: "上海",
      city_code: "101020100",
      districts: [],
    }));
    const wrapper = await mountPicker();
    expect(wrapper.get('[data-testid="location-empty"]').text()).toContain("暂无区/镇数据");
  });

  it("emits remove from the x button", async () => {
    const wrapper = mount(LocationPicker, {
      props: { city: "上海", platform: "boss", modelValue: [], disabled: false },
    });
    await wrapper.get(".city-chip-remove").trigger("click");
    expect(wrapper.emitted("remove")).toHaveLength(1);
  });

  it("opens with keyboard Enter and Space", async () => {
    const wrapper = mount(LocationPicker, {
      props: { city: "上海", platform: "boss", modelValue: [], disabled: false },
    });
    await wrapper.get('[data-testid="city-chip-toggle"]').trigger("keydown", { key: "Enter" });
    expect(wrapper.find('[data-testid="location-panel"]').exists()).toBe(true);
    await wrapper.get('[data-testid="city-chip-toggle"]').trigger("keydown", { key: " " });
    expect(wrapper.get('[data-testid="city-chip-toggle"]').attributes("aria-expanded")).toBe("false");
  });

  it("closes when clicking outside the panel", async () => {
    const wrapper = mount(LocationPicker, {
      props: { city: "上海", platform: "boss", modelValue: [], disabled: false },
    });
    await wrapper.get('[data-testid="city-chip-toggle"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="location-panel"]').exists()).toBe(true);
    document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find('[data-testid="location-panel"]').exists()).toBe(false);
  });
});

describe("LocationPicker narrow viewport (B072)", () => {
  function setInnerWidth(width: number): void {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: width,
    });
  }

  function panelMetrics(wrapper: ReturnType<typeof mount<typeof LocationPicker>>) {
    const style = wrapper.get('[data-testid="location-panel"]').attributes("style") || "";
    const read = (prop: string) => {
      const match = style.match(new RegExp(`${prop}:\\s*([-\\d.]+)px`));
      return match ? Number(match[1]) : NaN;
    };
    return { left: read("left"), width: read("width"), top: read("top") };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 1024,
    });
  });

  it.each([500, 360, 300])("keeps the panel inside the viewport at %ipx width", async (width) => {
    setInnerWidth(width);
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker();
    const { left, width: panelWidth } = panelMetrics(wrapper);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + panelWidth).toBeLessThanOrEqual(width);
    expect(panelWidth).toBeLessThanOrEqual(width - 24);
  });

  it("locks the panel to min width and keeps full district labels at very narrow widths", async () => {
    setInnerWidth(300);
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker();
    const { width: panelWidth } = panelMetrics(wrapper);
    expect(panelWidth).toBe(300 - 24);
    const districtLabel = wrapper.get('[data-testid="location-district-310115"]').text();
    expect(districtLabel).toContain("浦东新区");
    expect(wrapper.get('[data-testid="location-district-310104"]').text()).toContain("徐汇区");
  });

  it("does not squeeze district labels into one character on the business grid (BOSS)", async () => {
    setInnerWidth(360);
    vi.stubGlobal("fetch", fetchMockFor("/api/location-catalog", BOSS_CATALOG));
    const wrapper = await mountPicker("boss");
    await wrapper.get('[data-testid="location-district-310115"]').trigger("click");
    const selected = wrapper.emitted("update:modelValue")!.at(-1)![0] as LocationCondition[];
    await wrapper.setProps({ modelValue: selected });
    const businessLabel = wrapper.get('[data-testid="location-business-310115-154"]').text();
    expect(businessLabel).toContain("北蔡");
  });
});
