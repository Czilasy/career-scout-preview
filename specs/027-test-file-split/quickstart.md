# Quickstart: 测试大文件拆分重构（027）验证指南

每批次交付后按序执行，全部通过才允许提交。命令以仓库根目录为工作目录（Git Bash 口径；PowerShell 等价替换 `$TEMP` 为 `$env:TEMP`）。

## 0. 基线（仅开工前一次，B0）

1. `git status` 干净（9 个在办脏文件已按用户确认的方式处置）。
2. 全量跑一次并记录结果（命令以仓库惯例实测确认；预期全绿）。基线不绿先停，查明原因。
3. 拍快照（总数 + 清单）到系统临时目录：

```bash
uv run python - <<'PY' > "$TEMP/cs_tests_inventory_baseline.txt"
import unittest

def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from walk(t)
        else:
            yield t

suite = unittest.defaultTestLoader.discover("tests")
ids = sorted(f"{t.__class__.__name__}.{t._testMethodName}" for t in walk(suite))
print(len(ids))
print("\n".join(ids))
PY
```

4. 记录首行总数（2026-08-28 实测 1786；以本次重测为准）。快照文件全程只读，任何批次不得改动。

## 1. 聚焦测试（本批新子目录）

```bash
uv run python -m unittest discover -s tests/<子目录>
```

预期：全绿。B1 首次实证该命令形态；若不通按 research R6 退化为全量，门禁不降。

## 2. 后端全量

按基线实测确认的全量入口跑一次。预期：全绿，通过/跳过构成与基线一致。

## 3. 清单对账（零差异才允许继续）

```bash
uv run python - <<'PY' > "$TEMP/cs_tests_inventory_now.txt"
import unittest

def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from walk(t)
        else:
            yield t

suite = unittest.defaultTestLoader.discover("tests")
ids = sorted(f"{t.__class__.__name__}.{t._testMethodName}" for t in walk(suite))
print(len(ids))
print("\n".join(ids))
PY
diff "$TEMP/cs_tests_inventory_baseline.txt" "$TEMP/cs_tests_inventory_now.txt"
```

预期：`diff` 无任何输出（总数与逐条完全一致）。

## 4. 行数核对

```bash
wc -l tests/<子目录>/*.py | sort -rn
```

预期：域文件 ≤2000；带理由豁免 ≤2200；`__init__.py` 与共享帮手模块不计入门禁但一并核对。

## 5. 卫生与改动范围

```bash
uv run python -m unittest tests.test_repo_hygiene
git diff --check && git status
git diff --stat -- webui/ scripts/ pyproject.toml uv.lock   # 预期：空输出
```

## 6. 提交

单批全绿后：`refactor(tests): <批次描述>`，不 push。快照临时文件与本次对账产物留在临时目录，批次收尾清理（自产临时文件当轮清理纪律）。

## 7. 终检（B8）

- 全仓测试文件行数终检：`wc -l tests/**/*.py tests/*.py`，无 >2000（豁免 ≤2200 带理由）。
- 清单终对账（步骤 3）+ 全量终跑（步骤 2）。
- 全程产品代码零改动复核（步骤 5 第三条，对照基线提交）。
- BACKLOG：B075 移入已完成归档；订正旧数字 2525 为基线实测值。
