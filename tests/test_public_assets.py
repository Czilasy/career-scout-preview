"""Static checks for public release assets added in feature 007."""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class ReleaseAssetTests(unittest.TestCase):
    def test_release_template_covers_required_sections(self):
        text = _read(".github/release-template.md")
        required = (
            "Windows 安装包",
            "macOS 安装包",
            "SHA256",
            "前置条件",
            "已知限制",
            "常见问题",
        )
        for token in required:
            self.assertIn(token, text, f"Release 模板缺少必需内容：{token}")

    def test_packaging_manual_references_release_template(self):
        text = _read("packaging/README.md")
        self.assertIn(".github/release-template.md", text)

    def test_ci_runs_backend_and_frontend_tests(self):
        text = _read(".github/workflows/ci.yml")
        for token in (
            "pull_request",
            "uv run python -m unittest discover -s tests",
            "npm ci",
            "npm test",
        ):
            self.assertIn(token, text, f"CI 工作流缺少：{token}")

    def test_contributing_describes_ci_gate(self):
        text = _read("CONTRIBUTING.md")
        self.assertIn("ci.yml", text)
        self.assertIn("阻断合并", text)

    def test_changelog_has_no_duplicate_items_across_versions(self):
        text = _read("CHANGELOG.md")
        sections: list[tuple[str, list[str]]] = []
        current: tuple[str, list[str]] | None = None
        for line in text.splitlines():
            match = re.match(r"^## \[([^\]]+)\]", line)
            if match:
                current = (match.group(1), [])
                sections.append(current)
                continue
            stripped = line.strip()
            if current and stripped.startswith("- "):
                current[1].append(stripped[2:].strip())

        seen: dict[str, str] = {}
        duplicates: list[str] = []
        for version, items in sections:
            for item in items:
                normalized = re.sub(r"[“”\"'，。；：、()（）]", "", item)
                normalized = re.sub(r"\s+", "", normalized)
                if not normalized:
                    continue
                if normalized in seen:
                    duplicates.append(f"{version} 与 {seen[normalized]} 重复：{item}")
                seen[normalized] = version
        self.assertEqual(duplicates, [], "CHANGELOG 相邻版本存在重复条目")

    def test_no_remote_font_references_in_source_or_build(self):
        source = _read("webui/src/styles/theme.css")
        self.assertNotIn("fonts.googleapis.com", source)
        dist = ROOT / "webui" / "dist"
        self.assertTrue(dist.is_dir(), "webui/dist 不存在，请先构建前端")
        offenders: list[str] = []
        for path in dist.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".css", ".js"}:
                if "fonts.googleapis.com" in path.read_text(encoding="utf-8", errors="ignore"):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "构建产物仍包含远程字体引用")


if __name__ == "__main__":
    unittest.main()
