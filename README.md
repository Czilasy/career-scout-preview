# Career Scout · BOSS直聘职位助手 v2.3

Career Scout 是一个基于 Chrome DevTools Protocol（CDP）的 BOSS直聘职位搜索与求职分析工具。它连接你本机已经登录的 Chrome，复用真实登录态抓取职位列表与 JD 详情，输出含明文薪资的 JSON / CSV 数据，并生成薪资分布、技能词频和求职材料优化提示词。

项目同时提供一个本地 Web 工作台，用于简历驱动的职位筛选、AI 语义评估、感兴趣/垃圾桶管理和断点续跑。所有数据默认保存在本机 `~/.career-scout`，AI Key 存入系统凭据库，不会写入项目文件或日志。

> 定位：个人求职分析工具，仅供学习与研究使用，不是大规模爬虫，也不会自动投递、自动联系招聘者或预测录用概率。

## 免责声明

本项目仅供学习和技术研究参考。使用前请阅读 [BOSS直聘用户协议](https://www.zhipin.com/about/protocol.html) 及相关法律法规，不要用于商业转售、恶意爬取或对目标网站造成负担。使用本项目产生的一切后果由使用者自行承担。

## 快速开始

### 环境要求

- Python 3.10+
- Chrome 浏览器
- 可选：Node.js 18+（仅在修改 WebUI 前端源码时需要）

### 安装

```bash
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt
# 或使用 uv
uv sync
```

### 启动专用 Chrome 并登录

```bash
python scripts/boss_cdp_raw.py --setup-chrome
```

脚本会启动一个独立的 BOSS 专用 Chrome。请在弹出的窗口中登录 zhipin.com；登录态保存在 `~/.career-scout/chrome-profile`，只需登录一次。

### 抓取职位

```bash
python scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3
```

结果默认写入 `~/.career-scout/job-result/boss_jobs_*.json`，使用 `--format csv` 可输出 CSV。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--keyword` | 搜索关键词，默认 `AI Agent` |
| `--city` | 城市名或城市码，默认上海 |
| `--pages` | 抓取页数 |
| `--start-page` | 从第几页开始，支持断点续抓 |
| `--detail` / `--no-detail` | 是否抓取 JD 详情，默认开启 |
| `--max-details` | 最多抓取多少个详情 |
| `--format` | 输出格式：`json` 或 `csv` |
| `--analysis` | 抓取后输出分析报告 |
| `--input` | 从已有 JSON 文件读取，跳过抓取 |
| `--check` | 环境诊断 |
| `--smoke-test` | 真实浏览器/API 冒烟测试，不写结果文件 |
| `--setup-chrome` / `--stop-chrome` | 启动 / 关闭专用 Chrome |
| `--list-cities` | 查看支持的城市列表 |

抓取被风控或验证码拦截时，脚本会停止并保留已抓数据，提示停在第几页和原因；问题解除后可使用 `--start-page` 从断点继续。

### 生成求职摘要与提示词

```bash
python scripts/job_summary.py
```

默认读取 `~/.career-scout/job-result` 下最新的 `boss_jobs_*.json`，输出岗位市场摘要和可复制的求职材料优化提示词；`--prompt-only` 只输出提示词。

### 启动 Web 工作台

```bash
python webui/app.py
```

浏览器访问 `http://127.0.0.1:5000`。Windows 用户也可以直接双击 `tools/start.bat`，脚本会自动检查前端构建状态并处理旧服务进程。

## Web 工作台功能

- **岗位发现**：按“上传简历分析 → 确认搜索条件 → 粗筛/抓 JD/精筛 → 查看结果”推进，AI 筛选与抓取分开执行，不会合并成一次不可控的自动执行。
- **简历驱动的两层筛选**：第一层来自 BOSS 搜索结果，第二层是硬规则与 AI 语义评估；AI 不可用时可降级为人工填筛、跳过简历或仅硬规则。
- **结果区域**：符合/不符合为临时区，感兴趣/垃圾桶为持久区；结果按匹配、不匹配、待确认、已筛除分类展示。
- **断点续跑**：遇到验证码、登录过期、来源封禁或 AI 限流时会暂停并说明原因；服务重启后可从持久化断点继续，已抓列表、JD 和 AI 判定不会重复。
- **浏览器账号**：内置 A/B 账号，可添加自定义账号；每个账号使用独立 Chrome profile，登录态持久化。
- **高级设置与调优实验**：可调整列表抓取、JD 抓取和 AI 筛选参数；当前版本提供实验与验证框架，未预置未经验证的正式参数。
- **历史恢复**：可预演、准备并执行历史任务恢复；旧数据缺少具体失败原因时会明确标注，不猜造分类。

## 隐私与安全边界

- 职位数据、简历和 AI Key 只在本地处理；AI Key 通过系统凭据库保存（Windows Credential Manager / macOS Keychain / Linux Secret Service），不写入 SQLite、日志或导出文件。
- 简历读取、AI 设置读写等接口使用本地会话令牌保护。
- 前端只打开预期 BOSS 域名（`zhipin.com`）的 HTTPS 岗位链接。
- 项目不会自动投递、自动联系招聘者或预测录用概率。
- 失败状态如实展示：被风控、验证码、登录过期、限流等情况会区分原因，不会伪装成成功。

## 目录结构

```text
scripts/boss_cdp_raw.py   # CLI 抓取主入口
scripts/job_summary.py    # 抓取结果 → 求职摘要与提示词
data/city_codes.json      # 城市码表
webui/                    # Flask 后端 + Vue 3 前端源码
webui/dist/               # 已构建的前端产物，普通用户无需 Node.js
tests/                    # unittest，全 mock，无需真实 Chrome/网络
tools/start.bat           # Windows 一键启动 Web 工作台
pyproject.toml            # 打包配置，入口 career-scout / career-summary
requirements.txt          # Python 依赖
```

## 开发与测试

```bash
python -m unittest discover -s tests
cd webui
npm install
npm test
npm run build
```

修改前端源码后必须重新构建并提交 `webui/dist`，否则 Web 工作台可能使用旧构建产物。

## License

MIT License，详见 [LICENSE](./LICENSE)。
