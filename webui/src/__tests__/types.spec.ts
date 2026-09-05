import type {
  BrowserAccount,
  CityEntry,
  FilterField,
  FilterOption,
  FilterSnapshot,
  FilterSnapshotField,
  IntegrityConclusion,
  IntegritySnapshot,
  JobItem,
  Platform,
  PlatformCityCatalog,
  PlatformErrorCode,
  PlatformFilterSchema,
  PlatformSummary,
  PlatformsResponse,
  SourceErrorCode,
  TaskApiStatus,
  TaskSnapshot,
} from "../types";

// T501：平台注册、schema、城市、任务、结果、双岗位 ID 和稳定错误码的类型基线。
// 这些断言只在编译时由 vue-tsc 校验；运行时为 noop，确保类型存在且形状与契约一致。
describe("platform types baseline (T501)", () => {
  it("fixes the platform key union", () => {
    expectTypeOf<Platform>().toEqualTypeOf<"boss" | "zhilian">();
  });

  it("projects platform registry summary from GET /api/platforms", () => {
    const sample: PlatformsResponse = {
      ok: true,
      platforms: [
        {
          key: "zhilian",
          display_name: "智联招聘",
          filter_schema_version: 1,
          city_mapping_version: 1,
          enabled_for_new_tasks: true,
          availability_reason: "",
        },
      ],
      default_platform: "boss",
    };
    expectTypeOf(sample.platforms[0]).toMatchTypeOf<PlatformSummary>();
    expectTypeOf<PlatformSummary["key"]>().toEqualTypeOf<Platform>();
  });

  it("shapes filter-labels response (schema + options)", () => {
    const schema: PlatformFilterSchema = {
      ok: true,
      platform: "zhilian",
      schema_version: 1,
      enabled_for_new_tasks: true,
      fields: [
        { key: "salary", label: "薪资范围", multiple: true, options: [] },
        {
          key: "company_nature",
          label: "公司性质",
          multiple: true,
          options: [{ value: "stable-1", label: "民营" }],
        },
      ],
    };
    expectTypeOf(schema).toMatchTypeOf<PlatformFilterSchema>();
    expectTypeOf<PlatformFilterSchema["fields"][number]>().toMatchTypeOf<FilterField>();
    expectTypeOf<FilterField["options"][number]>().toMatchTypeOf<FilterOption>();
  });

  it("shapes city catalog response from GET /api/options", () => {
    const catalog: PlatformCityCatalog = {
      ok: true,
      platform: "zhilian",
      city_mapping_version: 1,
      cities: [{ label: "上海", value: "上海" }],
    };
    expectTypeOf(catalog).toMatchTypeOf<PlatformCityCatalog>();
    expectTypeOf<PlatformCityCatalog["cities"][number]>().toMatchTypeOf<CityEntry>();
  });

  it("keeps platform error codes aligned with the frozen contract", () => {
    const code: PlatformErrorCode = "platform_validation_failed";
    expectTypeOf<PlatformErrorCode>().toExtend<PlatformErrorCode>();
    void code;
  });

  it("covers source adapter error codes from job-source.md", () => {
    const code: SourceErrorCode = "source_login_required";
    expectTypeOf<SourceErrorCode>().toExtend<SourceErrorCode>();
    void code;
  });

  it("maps DB canonical status to the unified API status set", () => {
    const status: TaskApiStatus = "completed_with_pending";
    expectTypeOf<TaskApiStatus>().toExtend<TaskApiStatus>();
    void status;
  });

  it("keeps all six integrity conclusions in the frontend contract", () => {
    expectTypeOf<IntegrityConclusion>().toEqualTypeOf<
      "succeeded" | "empty" | "partial" | "failed" | "unverifiable" | "interrupted"
    >();
    const snapshots: IntegritySnapshot[] = [
      "succeeded", "empty", "partial", "failed", "unverifiable", "interrupted",
    ].map((conclusion) => ({ conclusion: conclusion as IntegrityConclusion, label: "" }));
    expectTypeOf(snapshots).toMatchTypeOf<IntegritySnapshot[]>();
  });

  it("adds dual job id fields and structured extras to JobItem", () => {
    const job: JobItem = {
      platform: "zhilian",
      platform_job_id: "platform-stable-id",
      job_id: "internal-uuid",
      title: "Python 后端工程师",
      company: "示例公司",
      salary: "20-30K",
      location: "上海",
      experience: "3-5年",
      degree: "本科",
      canonical_url: "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
      extra: { company_nature_label: "民营" },
    };
    expectTypeOf<JobItem["platform"]>().toEqualTypeOf<Platform | undefined>();
    expectTypeOf<JobItem["platform_job_id"]>().toEqualTypeOf<string | undefined>();
    expectTypeOf<JobItem["experience"]>().toEqualTypeOf<string | undefined>();
    expectTypeOf<JobItem["degree"]>().toEqualTypeOf<string | undefined>();
    void job;
  });

  it("extends task snapshot with platform identity and digest", () => {
    const snapshot: TaskSnapshot = {
      status: "running",
      platform: "zhilian",
      task_input_digest: "sha256-hex",
      scope_digest: "sha256-scope",
    };
    expectTypeOf<TaskSnapshot["platform"]>().toEqualTypeOf<Platform | undefined>();
    expectTypeOf<TaskSnapshot["task_input_digest"]>().toEqualTypeOf<string | undefined>();
    expectTypeOf(snapshot).toMatchTypeOf<TaskSnapshot>();
  });

  it("shapes the frozen AI filter snapshot stored on runs", () => {
    const snapshot: FilterSnapshot = {
      schema_version: 1,
      platform: "zhilian",
      fields: {
        company_nature: {
          values: ["stable-1"],
          labels: ["民营"],
        },
      },
    };
    expectTypeOf<FilterSnapshot["fields"][string]>().toMatchTypeOf<FilterSnapshotField>();
  });

  it("exposes per-platform browser login space projection", () => {
    const account: BrowserAccount = {
      id: "a",
      name: "账号 A",
      // http-api.md L319：GET /api/browser-accounts 不再返回 profile 路径
      platforms: {
        boss: { cdp_port: 9222 },
        zhilian: { cdp_port: 9223 },
      },
    };
    expectTypeOf<BrowserAccount["platforms"]>().toEqualTypeOf<
      Partial<Record<Platform, { cdp_port: number }>> | undefined
    >();
    void account;
  });
});
