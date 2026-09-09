"""Platform-specific field mapping for the shared AI evaluator.

The screening pipeline owns one evaluator. This module owns only the small
normalization differences between a BOSS job payload and a Zhilian payload:
schema code/label lookup, source field aliases, salary parsing, and the
compact fields sent to the two AI stages.
"""

from __future__ import annotations

import re

from scripts import boss_cdp_raw as boss
from webui.core import salary_monthly_bounds
from webui.platforms import get_platform


_COMBINED_EXPERIENCE_CODES = {"在校/应届": ("108", "102")}


class PlatformAiAdapter:
    key = "boss"
    filter_fields = ("experience", "degree", "scale", "stage", "industry")

    def option_maps(self, field: str) -> tuple[dict[str, str], dict[str, str]]:
        mapping = {
            "salary": boss.SALARY_MAP,
            "experience": boss.EXPERIENCE_MAP,
            "degree": boss.DEGREE_MAP,
            "industry": boss.INDUSTRY_MAP,
            "scale": boss.SCALE_MAP,
            "stage": boss.STAGE_MAP,
        }.get(field, {})
        return mapping, {code: label for label, code in mapping.items()}

    def value_code(self, value: object, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        labels, _codes = self.option_maps(field)
        return str(labels.get(text, text))

    def value_label(self, value: object, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        _labels, codes = self.option_maps(field)
        return str(codes.get(text, text))

    def criteria_codes(self, values: object, field: str) -> set[str]:
        return {
            code for value in (values or [])
            if (code := self.value_code(value, field))
        }

    def criteria_labels(self, values: object, field: str) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            label = self.value_label(value, field)
            if label and label not in seen:
                labels.append(label)
                seen.add(label)
        return labels

    def job_field_text(self, job: dict, field: str) -> str:
        return str(job.get(f"company_{field}") or "").strip()

    def is_unrestricted(self, field: str, code: object) -> bool:
        return str(code) == "0"

    def screen_hard_fields_text(self) -> str:
        return "薪资/经验/学历/规模/融资/行业"

    def screen_input_note(self) -> str:
        return ""

    def screen_fields(self, job: dict) -> tuple[str, ...]:
        return (
            job.get("job_labels", "") or "",
            job.get("company_scale", "") or "",
        )

    def detail_fields(self, job: dict) -> dict[str, str]:
        return {}

    def degree_code_label(self, code: object) -> str:
        for label, mapped in boss.DEGREE_MAP.items():
            if mapped == code:
                return label
        return str(code)

    def build_criteria_description(self, criteria: dict) -> str:
        lines: list[str] = []
        summary = (criteria.get("profile_summary") or "").strip()
        if summary:
            lines.append(f"候选人画像（仅用于放宽，不作为硬条件）：{summary}")
        if criteria.get("city"):
            lines.append("期望城市：" + "、".join(criteria["city"]))
        if criteria.get("degree"):
            lines.append("候选人学历：" + "、".join(
                self.degree_code_label(code) for code in criteria["degree"]
            ))
        fields = (
            ("salary", boss.SALARY_MAP, "期望薪资"),
            ("experience", boss.EXPERIENCE_MAP, "经验要求"),
            ("industry", boss.INDUSTRY_MAP, "期望行业"),
            ("scale", boss.SCALE_MAP, "期望公司规模"),
            ("stage", boss.STAGE_MAP, "期望融资阶段"),
        )
        for key, mapping, label in fields:
            codes = criteria.get(key) or []
            names = [name for name, code in mapping.items() if code in codes]
            if names:
                lines.append(f"{label}：" + "、".join(names))
        return "\n".join(lines) if lines else "（无明确标准，宽松判断）"

    def salary_selected_bounds(self, selected_codes: object) -> list[tuple[float, float | None]]:
        bounds: list[tuple[float, float | None]] = []
        for label, code in boss.SALARY_MAP.items():
            if code not in selected_codes or label == "不限":
                continue
            if label == "3K以下": bounds.append((0.0, 3.0))
            elif label == "3-5K": bounds.append((3.0, 5.0))
            elif label == "5-10K": bounds.append((5.0, 10.0))
            elif label == "10-20K": bounds.append((10.0, 20.0))
            elif label == "20-50K": bounds.append((20.0, 50.0))
            elif label == "50K以上": bounds.append((50.0, None))
        return bounds

    def salary_hard_mismatch(self, salary_text: object, selected_codes: object) -> bool:
        if not selected_codes:
            return False
        job_bounds = salary_monthly_bounds(salary_text)
        if job_bounds is None:
            return False
        selected_bounds = self.salary_selected_bounds(selected_codes)
        if not selected_bounds:
            return False
        job_low, job_high = job_bounds
        return not any(
            job_low < (high if high is not None else float("inf"))
            and job_high >= low
            for low, high in selected_bounds
        )

    def job_experience_codes(self, job: dict) -> set[str]:
        codes: set[str] = set()
        for part in self._job_filter_tag_text(job).split("|"):
            token = part.strip()
            if token in boss.EXPERIENCE_MAP:
                codes.add(boss.EXPERIENCE_MAP[token])
            elif token in _COMBINED_EXPERIENCE_CODES:
                codes.update(_COMBINED_EXPERIENCE_CODES[token])
        return codes

    def job_degree_codes(self, job: dict) -> set[str]:
        return {
            boss.DEGREE_MAP[token]
            for token in (part.strip() for part in self._job_filter_tag_text(job).split("|"))
            if token in boss.DEGREE_MAP
        }

    def job_scale_codes(self, job: dict) -> set[str]:
        code = boss.SCALE_MAP.get((job.get("company_scale") or "").strip())
        return {code} if code else set()

    def job_stage_codes(self, job: dict) -> set[str]:
        code = boss.STAGE_MAP.get((job.get("company_stage") or "").strip())
        return {code} if code else set()

    def job_industry_codes(self, job: dict) -> set[str]:
        industry = (job.get("company_industry") or "").strip()
        if industry in boss.INDUSTRY_MAP:
            return {boss.INDUSTRY_MAP[industry]}
        for name, code in boss.INDUSTRY_MAP.items():
            if name and name in industry:
                return {code}
        return set()

    def job_company_nature_codes(self, _job: dict) -> set[str]:
        return set()

    def job_codes(self, job: dict, field: str) -> set[str]:
        readers = {
            "experience": self.job_experience_codes,
            "degree": self.job_degree_codes,
            "scale": self.job_scale_codes,
            "stage": self.job_stage_codes,
            "industry": self.job_industry_codes,
            "company_nature": self.job_company_nature_codes,
        }
        reader = readers.get(field)
        return reader(job) if reader is not None else set()

    def job_value_label(self, job: dict, field: str) -> str:
        if field in ("experience", "degree"):
            mapping = boss.EXPERIENCE_MAP if field == "experience" else boss.DEGREE_MAP
            codes = self.job_experience_codes(job) if field == "experience" else self.job_degree_codes(job)
            parts = []
            for token in (part.strip() for part in self._job_filter_tag_text(job).split("|")):
                is_combined = field == "experience" and token in _COMBINED_EXPERIENCE_CODES
                if is_combined or mapping.get(token) in codes:
                    parts.append(token)
            return "、".join(dict.fromkeys(parts))
        return str(job.get(f"company_{field}") or "").strip()

    @staticmethod
    def _job_filter_tag_text(job: dict) -> str:
        return " | ".join(
            str(value).strip() for value in (
                job.get("tags"), job.get("job_labels"),
                job.get("jobExperience"), job.get("jobDegree"),
                job.get("tags_list"),
            ) if str(value or "").strip()
        )


class ZhilianAiAdapter(PlatformAiAdapter):
    key = "zhilian"
    filter_fields = ("experience", "degree", "scale", "industry", "company_nature")

    _UNRESTRICTED_CODES = {
        "salary": frozenset({"0000,9999999"}),
        "experience": frozenset({"-99", "-1"}),
        "degree": frozenset({"-1"}),
        "industry": frozenset({"-1"}),
        "scale": frozenset({"-1"}),
        "company_nature": frozenset(),
    }

    def option_maps(self, field: str) -> tuple[dict[str, str], dict[str, str]]:
        schema_field = get_platform(self.key).filter_schema.get_field(field)
        if schema_field is None:
            raise ValueError(
                f"platform schema missing field: {self.key}.{field}"
            )
        code_to_label = {option.value: option.label for option in schema_field.options}
        return ({label: code for code, label in code_to_label.items()}, code_to_label)

    def job_field_text(self, job: dict, field: str) -> str:
        extra = job.get("extra") if isinstance(job.get("extra"), dict) else {}
        keys = {
            "experience": ("experience", "jobExperience"),
            "degree": ("degree", "jobDegree"),
            "scale": ("company_scale", "companySize", "company_size"),
            "industry": ("company_industry", "industry", "industryName"),
            "company_nature": ("company_nature", "company_nature_label", "propertyName"),
        }.get(field, ())
        for key in keys:
            value = job.get(key) or extra.get(key)
            if str(value or "").strip():
                return str(value).strip()
        return ""

    def value_code(self, value: object, field: str) -> str:
        return super().value_code(value, field)

    def is_unrestricted(self, field: str, code: object) -> bool:
        return str(code) in self._UNRESTRICTED_CODES.get(field, frozenset())

    def screen_hard_fields_text(self) -> str:
        return "薪资/经验/学历/规模/行业/公司性质"

    def screen_input_note(self) -> str:
        return "；当前平台还会提供行业和公司性质"

    def screen_fields(self, job: dict) -> tuple[str, ...]:
        return (
            self.job_field_text(job, "degree"),
            self.job_field_text(job, "experience"),
            self.job_field_text(job, "scale"),
            self.job_field_text(job, "industry"),
            self.job_field_text(job, "company_nature"),
        )

    def detail_fields(self, job: dict) -> dict[str, str]:
        degree, experience, scale, industry, nature = self.screen_fields(job)
        return {
            "experience": experience,
            "degree": degree,
            "company_scale": scale,
            "industry": industry,
            "company_nature": nature,
        }

    def degree_code_label(self, code: object) -> str:
        return self.value_label(code, "degree")

    def build_criteria_description(self, criteria: dict) -> str:
        lines: list[str] = []
        summary = (criteria.get("profile_summary") or "").strip()
        if summary:
            lines.append(f"候选人画像（仅用于放宽，不作为硬条件）：{summary}")
        if criteria.get("city"):
            lines.append("期望城市：" + "、".join(criteria["city"]))
        if criteria.get("degree"):
            lines.append("候选人学历：" + "、".join(
                self.degree_code_label(code) for code in criteria["degree"]
            ))
        for key, label in (
            ("salary", "期望薪资"), ("experience", "经验要求"),
            ("industry", "期望行业"), ("scale", "期望公司规模"),
            ("company_nature", "公司性质"),
        ):
            names = self.criteria_labels(criteria.get(key), key)
            if names:
                lines.append(f"{label}：" + "、".join(names))
        return "\n".join(lines) if lines else "（无明确标准，宽松判断）"

    def salary_selected_bounds(self, selected_codes: object) -> list[tuple[float, float | None]]:
        bounds: list[tuple[float, float | None]] = []
        for code in selected_codes:
            if self.is_unrestricted("salary", code):
                continue
            try:
                low_text, high_text = str(code).split(",", 1)
                low = float(int(low_text) / 1000)
                high_value = int(high_text)
                high = None if high_value >= 9999999 else float(high_value / 1000)
            except (TypeError, ValueError):
                continue
            bounds.append((low, high))
        return bounds

    def salary_hard_mismatch(self, salary_text: object, selected_codes: object) -> bool:
        if not selected_codes:
            return False
        job_bounds = salary_monthly_bounds(salary_text)
        if job_bounds is None:
            text = str(salary_text or "")
            match = re.search(r"(\d+(?:\.\d+)?)\s*K\s*-\s*(\d+(?:\.\d+)?)\s*K", text, re.I)
            if match:
                job_bounds = (float(match.group(1)), float(match.group(2)))
            else:
                match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元(?:/月)?", text)
                if match:
                    job_bounds = (float(match.group(1)) / 1000, float(match.group(2)) / 1000)
        if job_bounds is None:
            return False
        selected_bounds = self.salary_selected_bounds(selected_codes)
        if not selected_bounds:
            return False
        job_low, job_high = job_bounds
        return not any(
            job_low < (high if high is not None else float("inf"))
            and job_high >= low
            for low, high in selected_bounds
        )

    def job_experience_codes(self, job: dict) -> set[str]:
        values = [self.job_field_text(job, "experience"), *self._job_filter_tag_text(job).split("|")]
        return {code for value in values if (code := self.value_code(value, "experience"))}

    def job_degree_codes(self, job: dict) -> set[str]:
        values = [self.job_field_text(job, "degree"), *self._job_filter_tag_text(job).split("|")]
        return {code for value in values if (code := self.value_code(value, "degree"))}

    def job_scale_codes(self, job: dict) -> set[str]:
        code = self.value_code(self.job_field_text(job, "scale"), "scale")
        return {code} if code else set()

    def job_stage_codes(self, _job: dict) -> set[str]:
        return set()

    def job_industry_codes(self, job: dict) -> set[str]:
        code = self.value_code(self.job_field_text(job, "industry"), "industry")
        return {code} if code else set()

    def job_company_nature_codes(self, job: dict) -> set[str]:
        code = self.value_code(self.job_field_text(job, "company_nature"), "company_nature")
        return {code} if code else set()

    def job_value_label(self, job: dict, field: str) -> str:
        return self.value_label(self.job_field_text(job, field), field)


_ADAPTERS: dict[str, PlatformAiAdapter] = {
    "boss": PlatformAiAdapter(),
    "zhilian": ZhilianAiAdapter(),
}


def resolve_platform_ai_adapter(platform: object = None, job: dict | None = None) -> PlatformAiAdapter:
    candidate = platform
    if not candidate and isinstance(job, dict):
        candidate = job.get("platform")
    return _ADAPTERS.get(str(candidate or "boss").strip().lower(), _ADAPTERS["boss"])
