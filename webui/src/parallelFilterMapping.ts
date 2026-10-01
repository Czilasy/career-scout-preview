import type {
  ConditionSnapshotV2,
  Platform,
  PlatformFilterSchema,
  PlatformFilterValues,
  UnifiedFilterField,
  UnifiedFilterValues,
} from "./types";

export const MAPPER_VERSION = "b096-v2-2026-09-27";
export const UNIFIED_FILTER_FIELDS = [
  "salary", "experience", "degree", "industry", "scale", "recruiter_activity",
] as const satisfies readonly UnifiedFilterField[];
export const UNIFIED_FIELD_LABELS: Record<UnifiedFilterField, string> = {
  salary: "薪资范围",
  experience: "经验要求",
  degree: "学历",
  industry: "行业",
  scale: "公司规模",
  recruiter_activity: "招聘者活跃",
};

type UnifiedSchema = Record<
  UnifiedFilterField,
  { label: string; multiple: boolean; options: Array<[string, string]> }
>;

export const UNIFIED_FILTER_SCHEMA: UnifiedSchema = {
  salary: {
    label: "薪资范围",
    multiple: true,
    options: [
      ["不限", "__unrestricted__"], ["5K 以下", "salary-below-5k"],
      ["5K-10K", "salary-5k-10k"], ["10K-20K", "salary-10k-20k"],
      ["20K-50K", "salary-20k-50k"], ["50K 以上", "salary-over-50k"],
    ],
  },
  experience: {
    label: "经验要求",
    multiple: true,
    options: [
      ["不限", "__unrestricted__"], ["经验不限", "experience-flexible"],
      ["1年以下", "experience-under-1y"], ["1-3年", "experience-1-3y"],
      ["3-5年", "experience-3-5y"], ["5-10年", "experience-5-10y"],
      ["10年以上", "experience-over-10y"],
    ],
  },
  degree: {
    label: "学历",
    multiple: true,
    options: [
      ["不限", "__unrestricted__"], ["初中及以下", "degree-junior"],
      ["高中", "degree-high"], ["中专/中技", "degree-secondary"],
      ["大专", "degree-college"], ["本科", "degree-bachelor"],
      ["硕士", "degree-master"], ["博士", "degree-doctor"],
    ],
  },
  industry: {
    label: "行业",
    multiple: true,
    options: [
      ["互联网/AI/软件/IT服务", "industry-internet"],
      ["电子商务/零售贸易", "industry-ecommerce"], ["金融", "industry-finance"],
      ["游戏/数字文娱", "industry-game"], ["企业/专业服务", "industry-enterprise"],
      ["教育培训", "industry-education"], ["社交网络", "industry-social"],
      ["医疗健康", "industry-medical"], ["生活服务", "industry-life"],
      ["广告营销/传媒", "industry-ads"],
    ],
  },
  scale: {
    label: "公司规模",
    multiple: true,
    options: [
      ["20人以下", "scale-below-20"], ["20-99人", "scale-20-99"],
      ["100-499人", "scale-100-499"], ["500-999人", "scale-500-999"],
      ["1000-9999人", "scale-1000-9999"], ["10000人以上", "scale-over-10000"],
    ],
  },
  recruiter_activity: {
    label: "招聘者活跃",
    multiple: false,
    options: [
      ["近一周", "activity-week"], ["近一个月", "activity-month"],
      ["近三个月", "activity-quarter"], ["近半年", "activity-half-year"],
    ],
  },
};

type MappingTable = Record<UnifiedFilterField, Record<Platform, Record<string, string[]>>>;

const UNRESTRICTED = "__unrestricted__";
const MAPPED_FIELDS = new Set<UnifiedFilterField>(UNIFIED_FILTER_FIELDS);
const EXCLUSIVE_KEYS: Record<Platform, string[]> = { boss: ["stage"], zhilian: ["company_nature"] };
const EXCLUSIVE_ALL: Record<Platform, PlatformFilterValues> = {
  boss: { stage: [] }, zhilian: { company_nature: [] },
};

const MAPPING: MappingTable = {
  salary: {
    boss: {
      "5K 以下": ["3K以下", "3-5K"], "5K-10K": ["5-10K"], "10K-20K": ["10-20K"],
      "20K-50K": ["20-50K"], "50K 以上": ["50K以上"],
    },
    zhilian: {
      "5K 以下": ["4K以下", "4K-6K"], "5K-10K": ["4K-6K", "6K-8K", "8K-10K"],
      "10K-20K": ["10K-15K", "15K-25K"], "20K-50K": ["15K-25K", "25K-35K", "35K-50K"],
      "50K 以上": ["50K以上"],
    },
  },
  experience: {
    boss: {
      "经验不限": ["经验不限"], "1年以下": ["在校生", "应届生", "经验不限", "1年以内"],
      "1-3年": ["1-3年"], "3-5年": ["3-5年"], "5-10年": ["5-10年"], "10年以上": ["10年以上"],
    },
    zhilian: {
      "经验不限": ["经验不限"], "1年以下": ["经验不限", "1年以下"],
      "1-3年": ["1-3年"], "3-5年": ["3-5年"], "5-10年": ["5-10年"], "10年以上": ["10年以上"],
    },
  },
  degree: {
    boss: {
      "初中及以下": ["初中及以下"], "高中": ["高中"], "中专/中技": ["中专/中技"],
      "大专": ["大专"], "本科": ["本科"], "硕士": ["硕士"], "博士": ["博士"],
    },
    zhilian: {
      "初中及以下": ["初中及以下"], "高中": ["高中"], "中专/中技": ["中专/中技"],
      "大专": ["大专"], "本科": ["本科"], "硕士": ["硕士"], "博士": ["博士"],
    },
  },
  industry: {
    boss: {
      "互联网/AI/软件/IT服务": ["互联网"], "电子商务/零售贸易": ["电子商务"],
      "金融": ["金融"], "游戏/数字文娱": ["游戏"], "企业/专业服务": ["企业服务"],
      "教育培训": ["教育培训"], "社交网络": ["社交网络"], "医疗健康": ["医疗健康"],
      "生活服务": ["生活服务"], "广告营销/传媒": ["广告营销"],
    },
    zhilian: {
      "互联网/AI/软件/IT服务": ["互联网/AI/软件/IT服务"],
      "电子商务/零售贸易": ["互联网/AI/软件/IT服务", "批发/零售/贸易"],
      "金融": ["金融业"], "游戏/数字文娱": ["互联网/AI/软件/IT服务", "广告/传媒/文化/体育"],
      "企业/专业服务": ["互联网/AI/软件/IT服务", "专业服务"], "教育培训": ["教育/培训/科研"],
      "社交网络": ["互联网/AI/软件/IT服务"], "医疗健康": ["生物/制药/医疗/医美"],
      "生活服务": ["生活服务"], "广告营销/传媒": ["广告/传媒/文化/体育"],
    },
  },
  scale: {
    boss: {
      "20人以下": ["0-20人"], "20-99人": ["20-99人"], "100-499人": ["100-499人"],
      "500-999人": ["500-999人"], "1000-9999人": ["1000-9999人"], "10000人以上": ["10000人以上"],
    },
    zhilian: {
      "20人以下": ["20人以下"], "20-99人": ["20-99人"],
      "100-499人": ["100-299人", "300-499人"], "500-999人": ["500-999人"],
      "1000-9999人": ["1000-9999人"], "10000人以上": ["10000人以上"],
    },
  },
  recruiter_activity: {
    boss: { "近一周": ["近一周"], "近一个月": ["近一个月"], "近三个月": ["近三个月"], "近半年": ["近半年"] },
    zhilian: { "近一周": ["近一周"], "近一个月": ["近一个月"], "近三个月": ["近三个月"], "近半年": ["近半年"] },
  },
};
function normalizeList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim()))];
}

/**
 * 各平台每个共同字段的「字段级不限制」码，取自两平台已冻结的筛选 schema
 * （``webui/platforms_boss.py`` 复用 ``scripts/boss/constants.py`` 码表、
 * ``webui/platforms_zhilian.py`` 的 ``_ZHILIAN_FIELD_OPTIONS``）。
 * 命中该码即表示该字段不增加限制，最终取值只保留这一个码、不再叠加具体档；
 * 岗位自身属性档（智联经验 ``-1``「经验不限」、BOSS 经验 ``101``「经验不限」）
 * 不在表内，按普通档参与硬筛。
 */
const FIELD_UNRESTRICTED_CODES: Record<Platform, Partial<Record<UnifiedFilterField, readonly string[]>>> = {
  boss: { salary: ["0"], experience: ["0"], degree: ["0"] },
  zhilian: {
    salary: ["0000,9999999"], experience: ["-99"], degree: ["-1"],
    industry: ["-1"], scale: ["-1"],
  },
};

function dropTiersBelowUnrestricted(
  platform: Platform,
  field: UnifiedFilterField,
  values: string[],
): string[] {
  if (values.length < 2) return values;
  const unrestricted = FIELD_UNRESTRICTED_CODES[platform][field];
  if (!unrestricted || !unrestricted.length) return values;
  const sentinel = values.find((value) => unrestricted.includes(value));
  return sentinel === undefined ? values : [sentinel];
}

export function normalizeUnifiedValues(raw: Partial<UnifiedFilterValues> | null | undefined): UnifiedFilterValues {
  const next = {} as UnifiedFilterValues;
  for (const field of UNIFIED_FILTER_FIELDS) next[field] = normalizeList(raw?.[field]);
  for (const field of UNIFIED_FILTER_FIELDS) {
    if (next[field].includes(UNIFIED_FIELD_LABELS[field] === "薪资范围" ? "不限" : "不限")) {
      next[field] = [];
    }
  }
  return next;
}

export function normalizePlatformValues(
  platform: Platform,
  raw: PlatformFilterValues | null | undefined,
): PlatformFilterValues {
  const next: PlatformFilterValues = { ...EXCLUSIVE_ALL[platform] };
  for (const [key, value] of Object.entries(raw || {})) {
    // A platform layer is an opaque draft except for the six fields that the
    // mapper owns. Preserve every array-valued field so platform-only schema
    // additions survive a later unified edit and a V2 snapshot round-trip.
    if (Array.isArray(value)) next[key] = normalizeList(value);
  }
  for (const [key, values] of Object.entries(next)) {
    if (!MAPPED_FIELDS.has(key as UnifiedFilterField)) continue;
    next[key] = dropTiersBelowUnrestricted(platform, key as UnifiedFilterField, values);
  }
  return next;
}

function optionValues(schema: PlatformFilterSchema | undefined, field: string): Array<{ label: string; value: string }> {
  const fields = (schema as unknown as { fields?: Array<{ key: string; options?: Array<{ label?: unknown; value?: unknown }> }> })?.fields;
  if (!Array.isArray(fields)) return [];
  const target = fields.find((item) => item && item.key === field);
  if (!target || !Array.isArray(target.options)) return [];
  return target.options.filter((item): item is { label: string; value: string } =>
    Boolean(item) && typeof item.label === "string" && typeof item.value === "string");
}

function platformFieldValues(
  platform: Platform,
  field: UnifiedFilterField,
  unified: UnifiedFilterValues,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): string[] {
  const options = optionValues(schemas[platform], field);
  const byLabel = new Map(options.map((option) => [option.label, option.value]));
  if (!unified[field].length) return [];
  const selected: string[] = [];
  for (const label of unified[field]) {
    if (label === "不限") return [];
    const mappedLabels = MAPPING[field][platform]?.[label];
    if (!mappedLabels) throw new Error(`平台筛选条件已变化，请更新后重试（${field}: ${label}）`);
    for (const mappedLabel of mappedLabels) {
      const value = byLabel.get(mappedLabel);
      if (value === undefined) {
        throw new Error(`平台筛选条件已变化，请更新后重试（${platform} ${field}: ${mappedLabel}）`);
      }
      selected.push(value);
    }
  }
  return [...new Set(selected)];
}

export function resolveMappedValues(
  platform: Platform,
  unified: Partial<UnifiedFilterValues> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): PlatformFilterValues {
  const normalized = normalizeUnifiedValues(unified);
  const next = { ...EXCLUSIVE_ALL[platform] };
  for (const field of UNIFIED_FILTER_FIELDS) {
    next[field] = platformFieldValues(platform, field, normalized, schemas);
  }
  return next;
}

export function applyUnifiedToPlatforms(
  unified: Partial<UnifiedFilterValues> | null | undefined,
  current: Partial<Record<Platform, PlatformFilterValues>> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): Record<Platform, PlatformFilterValues> {
  const mapped = {
    boss: resolveMappedValues("boss", unified, schemas),
    zhilian: resolveMappedValues("zhilian", unified, schemas),
  };
  const merge = (platform: Platform): PlatformFilterValues => {
    const next = normalizePlatformValues(platform, current?.[platform]);
    for (const field of UNIFIED_FILTER_FIELDS) next[field] = mapped[platform][field] || [];
    return next;
  };
  return {
    boss: merge("boss"),
    zhilian: merge("zhilian"),
  };
}

/** 只重算一个统一字段，保留两个平台其它字段和平台专属微调。 */
export function applyUnifiedFieldToPlatforms(
  field: UnifiedFilterField,
  values: string[] | null | undefined,
  current: Partial<Record<Platform, PlatformFilterValues>> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): Record<Platform, PlatformFilterValues> {
  const unifiedField = normalizeUnifiedValues({ [field]: values || [] });
  const next = {
    boss: normalizePlatformValues("boss", current?.boss),
    zhilian: normalizePlatformValues("zhilian", current?.zhilian),
  };
  for (const platform of ["boss", "zhilian"] as const) {
    const mapped = resolveMappedValues(platform, unifiedField, schemas);
    next[platform][field] = mapped[field] || [];
  }
  return next;
}

export function computeOverrides(
  unified: Partial<UnifiedFilterValues> | null | undefined,
  final: Partial<Record<Platform, PlatformFilterValues>> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): Record<Platform, PlatformFilterValues> {
  const mapped = {
    boss: resolveMappedValues("boss", unified, schemas),
    zhilian: resolveMappedValues("zhilian", unified, schemas),
  };
  const result = { boss: {}, zhilian: {} } as Record<Platform, PlatformFilterValues>;
  for (const platform of ["boss", "zhilian"] as const) {
    const platformFinal = normalizePlatformValues(platform, final?.[platform]);
    const platformMapped = mapped[platform];
    const overrides: PlatformFilterValues = {};
    for (const field of UNIFIED_FILTER_FIELDS) {
      const expected = platformMapped[field] || [];
      const actual = platformFinal[field] || [];
      if (actual.length !== expected.length || actual.some((item, index) => item !== expected[index])) {
        overrides[field] = actual;
      }
    }
    for (const [key, values] of Object.entries(platformFinal)) {
      if (MAPPED_FIELDS.has(key as UnifiedFilterField) || EXCLUSIVE_KEYS[platform].includes(key)) continue;
      overrides[key] = values;
    }
    result[platform] = overrides;
  }
  return result;
}

export function projectResumeSemantic(
  semantic: Record<string, unknown> | Partial<UnifiedFilterValues> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): { unifiedValues: UnifiedFilterValues; platformValues: Record<Platform, PlatformFilterValues> } {
  const unifiedValues = normalizeUnifiedValues(projectSemanticToUnified(semantic, schemas));
  return {
    unifiedValues,
    platformValues: applyUnifiedToPlatforms(unifiedValues, {}, schemas),
  };
}

function semanticFieldAlias(field: string): UnifiedFilterField | undefined {
  if (MAPPED_FIELDS.has(field as UnifiedFilterField)) return field as UnifiedFilterField;
  if (["recruiterActivity", "recruiter_activity", "招聘者活跃时间"].includes(field)) {
    return "recruiter_activity";
  }
  return undefined;
}

function projectSemanticToUnified(
  semantic: Record<string, unknown> | Partial<UnifiedFilterValues> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): UnifiedFilterValues {
  const next = {} as UnifiedFilterValues;
  for (const field of UNIFIED_FILTER_FIELDS) next[field] = [];
  if (!semantic || typeof semantic !== "object") return next;

  const append = (field: UnifiedFilterField, value: string) => {
    if (!next[field].includes(value)) next[field].push(value);
  };
  for (const [rawField, rawValues] of Object.entries(semantic)) {
    const values = normalizeList(rawValues);
    if (!values.length) continue;
    const directField = semanticFieldAlias(rawField);
    for (const field of UNIFIED_FILTER_FIELDS) {
      const canonicalLabels = new Set(UNIFIED_FILTER_SCHEMA[field].options.map(([label]) => label));
      for (const value of values) {
        if (directField === field && canonicalLabels.has(value)) append(field, value);
        const inferred = new Set<string>();
        for (const platform of ["boss", "zhilian"] as const) {
          const platformOptions = optionValues(schemas[platform], field);
          if (!platformOptions.some((option) => option.label === value)) continue;
          const mapping = MAPPING[field][platform] || {};
          for (const [canonical, mappedLabels] of Object.entries(mapping)) {
            if (mappedLabels.includes(value)) inferred.add(canonical);
          }
        }
        // 反向投影只认唯一命中：一个平台标签同时落在多个统一档上时一个都不写，
        // 让该字段保持不增加限制，也不把窄档塞进冻结快照。
        if (inferred.size === 1) append(field, [...inferred][0]);
      }
    }
  }
  return next;
}

export function buildConditionSnapshot(
  unified: Partial<UnifiedFilterValues> | null | undefined,
  final: Partial<Record<Platform, PlatformFilterValues>> | null | undefined,
  schemas: Partial<Record<Platform, PlatformFilterSchema>>,
): ConditionSnapshotV2 {
  const unifiedValues = normalizeUnifiedValues(unified);
  const platformValues = {
    boss: normalizePlatformValues("boss", final?.boss),
    zhilian: normalizePlatformValues("zhilian", final?.zhilian),
  };
  const exclusiveValues = {
    boss: Object.fromEntries(EXCLUSIVE_KEYS.boss.map((key) => [key, platformValues.boss[key] || []])),
    zhilian: Object.fromEntries(EXCLUSIVE_KEYS.zhilian.map((key) => [key, platformValues.zhilian[key] || []])),
  } as ConditionSnapshotV2["exclusiveValues"];
  return {
    snapshotVersion: 2,
    mappingVersion: MAPPER_VERSION,
    unifiedValues,
    platformValues,
    overrides: computeOverrides(unifiedValues, platformValues, schemas),
    exclusiveValues,
  };
}

export function validateConditionSnapshot(raw: unknown): ConditionSnapshotV2 {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("条件快照格式不正确");
  const value = raw as Record<string, unknown>;
  if (value.snapshotVersion !== 2) throw new Error("条件快照版本不受支持");
  if (typeof value.mappingVersion !== "string" || !value.mappingVersion.trim()) throw new Error("条件快照缺少映射版本");
  if (!value.unifiedValues || typeof value.unifiedValues !== "object" || Array.isArray(value.unifiedValues)) {
    throw new Error("条件快照缺少统一条件");
  }
  const unifiedRaw = value.unifiedValues as Record<string, unknown>;
  for (const field of UNIFIED_FILTER_FIELDS) {
    if (unifiedRaw[field] !== undefined && !Array.isArray(unifiedRaw[field])) {
      throw new Error(`条件快照字段格式不正确（${field}）`);
    }
  }
  const unifiedValues = normalizeUnifiedValues(unifiedRaw as Partial<UnifiedFilterValues>);

  function layer(
    container: unknown,
    platform: Platform,
    name: string,
    includeExclusiveDefaults = true,
  ): PlatformFilterValues {
    if (!container || typeof container !== "object" || Array.isArray(container)) {
      throw new Error(`条件快照缺少${name}`);
    }
    const rawLayer = (container as Record<string, unknown>)[platform];
    if (!rawLayer || typeof rawLayer !== "object" || Array.isArray(rawLayer)) {
      throw new Error(`条件快照缺少${name}.${platform}`);
    }
    for (const [key, list] of Object.entries(rawLayer as Record<string, unknown>)) {
      if (list !== undefined && !Array.isArray(list)) {
        throw new Error(`条件快照字段格式不正确（${name}.${platform}.${key}）`);
      }
    }
    const normalized = normalizePlatformValues(platform, rawLayer as PlatformFilterValues);
    if (!includeExclusiveDefaults) {
      for (const key of EXCLUSIVE_KEYS[platform]) delete normalized[key];
    }
    return normalized;
  }

  const platformValues = {
    boss: layer(value.platformValues, "boss", "platformValues"),
    zhilian: layer(value.platformValues, "zhilian", "platformValues"),
  };
  const overrides = {
    boss: layer(value.overrides, "boss", "overrides", false),
    zhilian: layer(value.overrides, "zhilian", "overrides", false),
  };
  if (!value.exclusiveValues || typeof value.exclusiveValues !== "object" || Array.isArray(value.exclusiveValues)) {
    throw new Error("条件快照缺少专属条件");
  }
  const exclusiveRaw = value.exclusiveValues as Record<string, unknown>;
  function exclusive(platform: Platform, key: string): string[] {
    const row = exclusiveRaw[platform];
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw new Error(`条件快照缺少exclusiveValues.${platform}`);
    }
    const list = (row as Record<string, unknown>)[key];
    if (list !== undefined && !Array.isArray(list)) {
      throw new Error(`条件快照字段格式不正确（exclusiveValues.${platform}.${key}）`);
    }
    return normalizeList(list);
  }
  return {
    snapshotVersion: 2,
    mappingVersion: value.mappingVersion,
    unifiedValues,
    platformValues,
    overrides,
    exclusiveValues: {
      boss: { stage: exclusive("boss", "stage") },
      zhilian: { company_nature: exclusive("zhilian", "company_nature") },
    },
  } as ConditionSnapshotV2;
}
