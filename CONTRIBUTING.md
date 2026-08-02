# 参与贡献

感谢你愿意参与 Career Scout。

## 开发环境

- Python 3.10+，建议使用 `uv`
- Node.js 20+（仅修改 WebUI 前端时需要）

## 本地验证

```bash
uv sync
uv run python -m unittest discover -s tests
```

修改前端后：

```bash
cd webui
npm ci
npm test
npm run build
```

构建产物 `webui/dist` 必须随改动一起提交。

## 提交 Pull Request

- 先说明要解决的问题和改动范围。
- 保持改动小而聚焦，不夹带无关重构。
- 提交前运行测试并确认通过。
- 不要在 issue、PR 或代码中提交真实账号、Cookie、简历或 API Key。

## 行为准则

请以尊重、专业和建设性的方式交流，不发表人身攻击或歧视性内容。
