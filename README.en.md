# Career Scout · BOSS Zhipin Job Assistant v2.3

Career Scout is a job-search and career-analysis tool for BOSS直聘 (zhipin.com), built on the Chrome DevTools Protocol (CDP). It connects to your already-logged-in Chrome, reuses the real session to scrape job listings and JD details, and writes JSON / CSV results with plaintext salaries. It also generates salary distributions, skill-frequency stats, and copy-paste prompts for polishing your job-application materials.

The project ships with a local web workbench for resume-driven screening, AI semantic evaluation, interested/trash management, and resumable tasks. Data is stored locally under `~/.career-scout`, and AI keys are saved through the system credential store rather than project files or logs.

> Positioning: a personal job-analysis tool for learning and research. It is not a large-scale crawler, and it never auto-applies, contacts recruiters, or predicts hiring probability.

## Disclaimer

This project is provided for learning and technical research only. Please read the [BOSS直聘 user agreement](https://www.zhipin.com/about/protocol.html) and applicable laws before using it. Do not use it for commercial resale, malicious scraping, or to put load on the target website. You are responsible for how you use this software.

## Quick Start

### Requirements

- Python 3.10+
- Chrome browser
- Optional: Node.js 18+ (only needed when modifying the WebUI frontend)

### Install

```bash
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt
# or with uv
uv sync
```

### Start the Dedicated Chrome and Log In

```bash
python scripts/boss_cdp_raw.py --setup-chrome
```

The script starts a dedicated BOSS Chrome window. Log in to zhipin.com in that window; the session is stored under `~/.career-scout/chrome-profile` and only needs to be created once.

### Scrape Jobs

```bash
python scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3
```

Results are written to `~/.career-scout/job-result/boss_jobs_*.json` by default. Use `--format csv` for CSV output.

Common options:

| Option | Description |
| --- | --- |
| `--keyword` | Search keyword, default `AI Agent` |
| `--city` | City name or city code, default 上海 |
| `--pages` | Number of pages to scrape |
| `--start-page` | Resume from a specific page |
| `--detail` / `--no-detail` | Scrape JD details, enabled by default |
| `--max-details` | Maximum number of details to scrape |
| `--format` | Output format: `json` or `csv` |
| `--analysis` | Print an analysis report after scraping |
| `--input` | Read from an existing JSON file, skipping scraping |
| `--check` | Run environment diagnostics |
| `--smoke-test` | Real browser/API smoke test without writing result files |
| `--setup-chrome` / `--stop-chrome` | Start / stop the dedicated Chrome |
| `--list-cities` | List supported cities |

If the scraper is blocked by risk control or a captcha, it stops immediately, keeps already-scraped data, and tells you which page and why. Once the issue is resolved, use `--start-page` to resume.

### Generate a Summary and Prompt

```bash
python scripts/job_summary.py
```

It reads the newest `boss_jobs_*.json` from `~/.career-scout/job-result` and prints a market summary plus a copyable prompt. Use `--prompt-only` to print only the prompt.

### Start the Web Workbench

```bash
python webui/app.py
```

Open `http://127.0.0.1:5000`. On Windows you can also double-click `tools/start.bat`; the script checks the frontend build state and cleans up stale server processes automatically.

## Web Workbench Features

- **Job discovery**: a four-step flow of resume analysis, search confirmation, coarse/AI screening with JD scraping, and result review. Scraping and AI screening are separate actions, never merged into one uncontrolled run.
- **Resume-driven two-layer screening**: layer one comes from BOSS search results; layer two combines hard rules with AI semantic assessment. When AI is unavailable, the flow can degrade to manual filtering, skipping the resume, or hard rules only.
- **Result zones**: match/mismatch are temporary zones, while interested/trash are persistent zones. Results are shown as matched, mismatched, pending review, or filtered out.
- **Resumable tasks**: captcha, login expiry, source blocking, and AI rate limits pause the task with a clear reason. Tasks can resume from persisted checkpoints after a service restart without redoing scraped listings, JDs, or AI decisions.
- **Browser accounts**: built-in A/B accounts plus custom accounts, each with its own persistent Chrome profile.
- **Advanced settings and tuning experiments**: control list scraping, JD scraping, and AI screening parameters. The current release provides the experiment framework, not unverified final parameters.
- **Historical recovery**: preview, prepare, and execute recovery of historical runs. Old data without concrete failure evidence is labeled clearly instead of guessed.

## Privacy and Safety Boundaries

- Job data, resumes, and AI keys are processed locally. AI keys are stored via the system credential store (Windows Credential Manager / macOS Keychain / Linux Secret Service), never in SQLite, logs, or export files.
- Resume reads and AI-setting endpoints are protected by a local session token.
- The frontend only opens HTTPS job links on the expected BOSS domain (`zhipin.com`).
- The project never auto-applies, contacts recruiters, or predicts hiring probability.
- Failures are reported honestly: risk control, captchas, login expiry, and rate limits are distinguished rather than masked as success.

## Project Layout

```text
scripts/boss_cdp_raw.py   # CLI scraper entry point
scripts/job_summary.py    # results → summary and prompt
data/city_codes.json      # city code table
webui/                    # Flask backend + Vue 3 frontend source
webui/dist/               # prebuilt frontend; no Node.js needed for normal use
tests/                    # unittest, fully mocked, no real Chrome/network
tools/start.bat           # Windows one-click WebUI launcher
pyproject.toml            # packaging; entry points career-scout / career-summary
requirements.txt          # Python dependencies
```

## Development and Testing

```bash
python -m unittest discover -s tests
cd webui
npm install
npm test
npm run build
```

After changing frontend source, rebuild and commit `webui/dist`; otherwise the workbench may serve stale assets.

## License

MIT License, see [LICENSE](./LICENSE).
