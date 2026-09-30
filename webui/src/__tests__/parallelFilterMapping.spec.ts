import { describe, expect, it } from "vitest";

import {
  buildConditionSnapshot,
  computeOverrides,
  MAPPER_VERSION,
  normalizeUnifiedValues,
  projectResumeSemantic,
  resolveMappedValues, applyUnifiedToPlatforms, applyUnifiedFieldToPlatforms, UNIFIED_FILTER_FIELDS,
  validateConditionSnapshot,
} from "../parallelFilterMapping";
import type { PlatformFilterSchema, PlatformFilterValues } from "../types";

function schema(platform: "boss" | "zhilian"): PlatformFilterSchema {
  const options: Record<"boss" | "zhilian", Record<string, Array<[string, string]>>> = {
    boss: {
      salary: [["3K以下", "1"], ["3-5K", "2"], ["5-10K", "3"], ["10-20K", "4"], ["20-50K", "406"], ["50K以上", "807"], ["不限", "0"]],
      experience: [["在校生", "101"], ["应届生", "102"], ["经验不限", "103"], ["1年以内", "104"], ["1-3年", "105"], ["3-5年", "106"], ["5-10年", "107"], ["10年以上", "108"], ["不限", "0"]],
      degree: [["初中及以下", "209"], ["高中", "208"], ["中专/中技", "207"], ["大专", "202"], ["本科", "203"], ["硕士", "204"], ["博士", "205"], ["不限", "0"]],
      industry: [["互联网", "100901"], ["电子商务", "100904"], ["金融", "100905"], ["游戏", "100907"], ["企业服务", "100906"], ["教育培训", "100908"], ["社交网络", "100909"], ["医疗健康", "100910"], ["生活服务", "100911"], ["广告营销", "100912"]],
      scale: [["0-20人", "801"], ["20-99人", "802"], ["100-499人", "803"], ["500-999人", "804"], ["1000-9999人", "805"], ["10000人以上", "806"]],
      recruiter_activity: [["近一周", "1"], ["近一个月", "2"], ["近三个月", "3"], ["近半年", "4"]],
      stage: [["A轮", "802"], ["B轮", "804"]],
    },
    zhilian: {
      salary: [["4K以下", "401"], ["4K-6K", "402"], ["6K-8K", "403"], ["8K-10K", "404"], ["10K-15K", "405"], ["15K-25K", "406"], ["25K-35K", "407"], ["35K-50K", "408"], ["50K以上", "409"], ["不限", "0"]],
      experience: [["经验不限", "103"], ["1年以下", "104"], ["1-3年", "105"], ["3-5年", "106"], ["5-10年", "107"], ["10年以上", "108"], ["全部", "0"]],
      degree: [["初中及以下", "209"], ["高中", "208"], ["中专/中技", "207"], ["大专", "202"], ["本科", "203"], ["硕士", "204"], ["博士", "205"], ["MBA/EMBA", "206"], ["不限", "0"]],
      industry: [["互联网/AI/软件/IT服务", "001"], ["批发/零售/贸易", "002"], ["金融业", "003"], ["广告/传媒/文化/体育", "004"], ["专业服务", "005"], ["教育/培训/科研", "006"], ["生物/制药/医疗/医美", "007"], ["生活服务", "008"]],
      scale: [["20人以下", "801"], ["20-99人", "802"], ["100-299人", "803"], ["300-499人", "804"], ["500-999人", "805"], ["1000-9999人", "806"], ["10000人以上", "807"]],
      recruiter_activity: [["近一周", "1"], ["近一个月", "2"], ["近三个月", "3"], ["近半年", "4"]],
      company_nature: [["国企", "1"], ["民营", "2"]],
    },
  };
  const keys = Object.keys(options[platform]);
  return {
    platform,
    schema_version: 1,
    fields: keys.map((key) => ({
      key,
      label: key,
      multiple: key !== "recruiter_activity",
      options: options[platform][key].map(([label, value]) => ({ label, value })),
    })),
  } as unknown as PlatformFilterSchema;
}

const boss = schema("boss");
const zhilian = schema("zhilian");
const schemas = { boss, zhilian };

describe("B096 V2 unified filter mapping", () => {
  it("exposes exactly six unified fields and the frozen mapping version", () => {
    expect(UNIFIED_FILTER_FIELDS).toEqual([
      "salary", "experience", "degree", "industry", "scale", "recruiter_activity",
    ]);
    expect(MAPPER_VERSION).toBe("b096-v2-2026-09-27");
  });

  it("covers every unified salary and experience option with exact labels", () => {
    const unified = {
      salary: ["5K 以下", "5K-10K", "10K-20K", "20K-50K", "50K 以上"],
      experience: ["经验不限", "1年以下", "1-3年", "3-5年", "5-10年", "10年以上"],
      degree: [], industry: [], scale: [], recruiter_activity: [],
    };
    const bossValues = resolveMappedValues("boss", unified, schemas);
    expect(bossValues.salary).toEqual(["1", "2", "3", "4", "406", "807"]);
    expect(bossValues.experience).toEqual(["103", "101", "102", "104", "105", "106", "107", "108"]);
    const zhilianValues = resolveMappedValues("zhilian", unified, schemas);
    expect(zhilianValues.salary).toEqual(["401", "402", "403", "404", "405", "406", "407", "408", "409"]);
    expect(zhilianValues.experience).toEqual(["103", "104", "105", "106", "107", "108"]);
  });

  it("maps multi-option industry and scale labels without guessing", () => {
    const unified = {
      salary: [], experience: [], degree: [],
      industry: ["电子商务/零售贸易", "社交网络"],
      scale: ["100-499人"],
      recruiter_activity: [],
    };
    expect(resolveMappedValues("boss", unified, schemas).industry).toEqual(["100904", "100909"]);
    expect(resolveMappedValues("zhilian", unified, schemas).industry).toEqual(["001", "002"]);
    expect(resolveMappedValues("zhilian", unified, schemas).scale).toEqual(["803", "804"]);
  });

  it("normalizes explicit unrestricted labels and deduplicates values", () => {
    const normalized = normalizeUnifiedValues({
      salary: ["不限", "5K-10K", "5K-10K", ""],
      experience: ["不限"],
      degree: [], industry: [], scale: [], recruiter_activity: ["近一周", "近一周"],
    });
    expect(normalized.salary).toEqual([]);
    expect(normalized.experience).toEqual([]);
    expect(normalized.recruiter_activity).toEqual(["近一周"]);
  });

  it("overrides only the changed unified field and keeps platform tuning", () => {
    let platform = resolveMappedValues("boss", {
      salary: ["5K-10K"], experience: ["1-3年"], degree: [], industry: [], scale: [], recruiter_activity: [],
    }, schemas);
    platform.salary = ["807"];
    let current = {
      boss: platform, zhilian: resolveMappedValues("zhilian", {
        salary: ["5K-10K"], experience: ["1-3年"], degree: [], industry: [], scale: [], recruiter_activity: [],
      }, schemas),
    };
    const nextUnified = {
      salary: ["10K-20K"], experience: ["1-3年"], degree: [], industry: [], scale: [], recruiter_activity: [],
    };
    current = applyUnifiedToPlatforms(nextUnified, current, schemas);
    expect(current.boss.salary).toEqual(["4"]);
    expect(current.boss.experience).toEqual(["105"]);
    expect(current.zhilian.salary).toEqual(["405", "406"]);
    expect(current.zhilian.experience).toEqual(["105"]);
  });

  it("keeps platform salary micro-tuning and exclusive fields when another unified field changes", () => {
    const current: Record<"boss" | "zhilian", PlatformFilterValues> = {
      boss: { ...resolveMappedValues("boss", { salary: ["5K-10K"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] }, schemas), stage: ["804"] },
      zhilian: { ...resolveMappedValues("zhilian", { salary: ["5K-10K"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] }, schemas), company_nature: ["1"] },
    };
    current.boss.salary = ["807"];
    const next = applyUnifiedToPlatforms({ salary: ["5K-10K"], experience: ["3-5年"], degree: [], industry: [], scale: [], recruiter_activity: [] }, current, schemas);

    expect(next.boss.salary).toEqual(["3"]);
    expect(next.boss.stage).toEqual(["804"]);
    expect(next.zhilian.company_nature).toEqual(["1"]);
  });

  it("preserves platform-only fields such as Zhilian MBA/EMBA and industry across unified edits and snapshots", () => {
    const unified = {
      salary: ["5K-10K"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [],
    };
    const current = {
      boss: {
        ...resolveMappedValues("boss", unified, schemas),
        stage: ["804"],
        boss_industry_tuning: ["互联网"],
      },
      zhilian: {
        ...resolveMappedValues("zhilian", unified, schemas),
        degree: ["206"],
        industry: ["002"],
        company_nature: ["1"],
        zhilian_industry_tuning: ["批发/零售/贸易"],
      },
    } satisfies Record<"boss" | "zhilian", PlatformFilterValues>;

    const next = applyUnifiedFieldToPlatforms(
      "experience",
      ["3-5年"],
      current,
      schemas,
    );
    expect(next.zhilian).toMatchObject({
      degree: ["206"],
      industry: ["002"],
      company_nature: ["1"],
      zhilian_industry_tuning: ["批发/零售/贸易"],
    });
    expect(next.boss).toMatchObject({ stage: ["804"], boss_industry_tuning: ["互联网"] });

    const snapshot = buildConditionSnapshot(
      { ...unified, experience: ["3-5年"] },
      next,
      schemas,
    );
    expect(snapshot.overrides.zhilian).toMatchObject({
      degree: ["206"],
      industry: ["002"],
      zhilian_industry_tuning: ["批发/零售/贸易"],
    });
    const restored = validateConditionSnapshot(snapshot);
    expect(restored.platformValues.zhilian).toMatchObject({
      degree: ["206"],
      industry: ["002"],
      company_nature: ["1"],
      zhilian_industry_tuning: ["批发/零售/贸易"],
    });
    expect(restored.platformValues.boss).toMatchObject({ stage: ["804"], boss_industry_tuning: ["互联网"] });
    expect(restored.overrides.zhilian).toMatchObject({
      degree: ["206"],
      industry: ["002"],
      zhilian_industry_tuning: ["批发/零售/贸易"],
    });
  });

  it("records only final values that differ from the deterministic mapping", () => {
    const unified = {
      salary: ["5K-10K"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [],
    };
    const mapped = {
      boss: resolveMappedValues("boss", unified, schemas),
      zhilian: resolveMappedValues("zhilian", unified, schemas),
    };
    const final = {
      boss: { ...mapped.boss, salary: ["1", "2", "3"] },
      zhilian: mapped.zhilian,
    };
    expect(computeOverrides(unified, final, schemas)).toEqual({
      boss: { salary: ["1", "2", "3"] },
      zhilian: {},
    });
  });

  it("keeps exclusive fields and projects resume semantic suggestions", () => {
    const projected = projectResumeSemantic({
      salary: ["5K-10K"], experience: ["1-3年"], degree: [], industry: [], scale: [], recruiter_activity: [],
    }, schemas);
    expect(projected.platformValues.boss.salary).toEqual(["3"]);
    expect(projected.platformValues.zhilian.salary).toEqual(["403", "404"]);
    const snapshot = buildConditionSnapshot(
      projected.unifiedValues,
      {
        boss: { ...projected.platformValues.boss, stage: ["804"] },
        zhilian: { ...projected.platformValues.zhilian, company_nature: ["1"] },
      },
      schemas,
    );
    expect(snapshot.exclusiveValues).toEqual({
      boss: { stage: ["804"] }, zhilian: { company_nature: ["1"] },
    });
  });

  it("projects platform-shaped resume semantics into only the six unified fields", () => {
    const projected = projectResumeSemantic({
      experience: ["3-5年"],
      stage: ["B轮"],
      company_nature: ["国企"],
      "招聘者活跃时间": ["近一个月"],
    }, schemas);

    expect(projected.unifiedValues.experience).toEqual(["3-5年"]);
    expect(projected.unifiedValues.recruiter_activity).toEqual(["近一个月"]);
    expect(projected.unifiedValues.salary).toEqual([]);
    expect(projected.platformValues.boss.experience).toEqual(["106"]);
    expect(projected.platformValues.zhilian.experience).toEqual(["106"]);
    expect(projected.platformValues.boss.stage).toEqual([]);
    expect(projected.platformValues.zhilian.company_nature).toEqual([]);
  });

  it("blocks startup when a frozen schema label is missing", () => {
    const broken = schema("boss");
    (broken.fields.find((field) => field.key === "salary") as unknown as {
      options: Array<{ label: string; value: string }>;
    }).options = broken.fields.find((field) => field.key === "salary")!.options
      .filter((option) => option.label !== "3-5K");
    expect(() => resolveMappedValues("boss", {
      salary: ["5K 以下"], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [],
    }, { ...schemas, boss: broken })).toThrowError(/平台筛选条件已变化/);
  });

  it("validates platform layers independently and preserves overrides and exclusive values", () => {
    const snapshot = validateConditionSnapshot({
      snapshotVersion: 2,
      mappingVersion: MAPPER_VERSION,
      unifiedValues: { salary: [], experience: [], degree: [], industry: [], scale: [], recruiter_activity: [] },
      platformValues: {
        boss: { salary: ["406"], stage: ["804"] },
        zhilian: { salary: ["405"], company_nature: ["1"] },
      },
      overrides: {
        boss: { salary: ["807"] },
        zhilian: { salary: ["409"] },
      },
      exclusiveValues: {
        boss: { stage: ["804"] },
        zhilian: { company_nature: ["1"] },
      },
    });

    expect(snapshot.platformValues.boss).toMatchObject({ salary: ["406"], stage: ["804"] });
    expect(snapshot.platformValues.zhilian).toMatchObject({ salary: ["405"], company_nature: ["1"] });
    expect(snapshot.overrides).toEqual({
      boss: { salary: ["807"] },
      zhilian: { salary: ["409"] },
    });
    expect(snapshot.exclusiveValues).toEqual({
      boss: { stage: ["804"] },
      zhilian: { company_nature: ["1"] },
    });
  });
});
