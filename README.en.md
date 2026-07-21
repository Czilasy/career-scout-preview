# BOSS Zhipin Scraper · Job Crawler v2.0 (Chrome CDP / Plaintext Salary)

> 🌐 中文文档：[README.md](./README.md)

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![Version](https://img.shields.io/badge/version-2.0.0-orange.svg)

A lightweight **BOSS Zhipin scraper / crawler** (a.k.a. spider) for job listings on [zhipin.com](https://www.zhipin.com). Instead of driving a heavy Selenium/Playwright browser, it connects to your **already-logged-in Chrome** via the Chrome DevTools Protocol (CDP), reuses the real session, and calls the in-page search API directly — bypassing the front-end font-based anti-scraping so you get the **plaintext salary** in every record. Output goes to JSON / CSV, plus an aggregated salary/skill analysis and a copy-paste prompt for polishing your job-application materials. Also ships as a Career Scout Agent Skill.

> 📌 **In one sentence**: no Selenium/Playwright — connect to your logged-in Chrome over CDP, hit the search API with the real session, get JSON/CSV with plaintext salaries, plus salary-distribution, skill-frequency stats and a résumé-optimization prompt.

---

## ⚠️ Disclaimer

This project is for **learning and technical research purposes only**. It is intended to explore Chrome DevTools Protocol, front-end anti-scraping mechanisms, and data-collection techniques. Do **not** use it for any purpose that violates the [BOSS Zhipin Terms of Service](https://www.zhipin.com/about/protocol.html) or applicable laws and regulations, including commercial resale, malicious scraping, or any activity that imposes undue load on the target site. Users are solely responsible for the consequences of using this project; the author is not liable for any misuse.

---

## 🚀 30-Second Quick Start

```bash
# 1. Clone + install deps
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt          # or: uv sync

# 2. Launch an isolated Chrome and log in (only once; session persists)
python3 scripts/boss_cdp_raw.py --setup-chrome

# 3. Scrape + analyze
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --analysis

# Cities nationwide are supported (incl. tier-3/4/5), e.g.:
python3 scripts/boss_cdp_raw.py --keyword "前端" --city 赣州 --pages 3
# List supported cities: --list-cities [keyword]
python3 scripts/boss_cdp_raw.py --list-cities 江

# 4. Generate an aggregated summary + prompt after scraping (reads the latest result)
python3 scripts/job_summary.py
```

Right after scraping you get salary ranges, experience requirements, top skill keywords, and an application-optimization prompt. The CLI prompt only uses scraped job data and never reads a local résumé. The optional AI Job Workbench parses your résumé, generates keywords, streams job cards, and learns from feedback — but it never auto-applies, contacts recruiters, or predicts hiring probability.

## AI Job Workbench

Workbench scraper tasks run through one controlled executor: every task has a total timeout and traceable failure code, cancellation terminates the child process tree, logs and artifacts are size-bounded, and artifacts must stay inside the task result directory. Job discovery only sends jobs with a valid BOSS HTTPS detail URL into detail fetching, AI assessment, and formal results.

Before fetching begins, job discovery checks the dedicated BOSS browser once. It reports that the dedicated browser is disconnected when Chrome/CDP is unavailable, and reports that BOSS login is required when the browser is connected without an active session. A failed preflight stops the run immediately instead of retrying every search term and ending with an unknown error.

After installing dependencies, start the local workspace:

```bash
python3 webui/app.py
```

Open `http://127.0.0.1:5000`. The root path `/` is the only supported frontend entry point; the repository no longer keeps parallel version pages. The workspace is a dark-themed job-seeking workbench: a collapsible left settings panel for profiles, resumes, and AI settings, and a main area with a single-column stream of fixed-height job cards.

### Core Capabilities

1. **Resume parsing**: Upload a TXT / PDF / DOCX résumé and the AI extracts job direction, city, skills, and up to 3 search keyword groups. The upload button reports analyzing, success, or retryable failure in place; without AI consent the file is saved locally and clearly marked as not analyzed instead of creating a permanently queued task. Users can manually supplement or override — **manual conditions always take priority**. Manual entry provides separate direction and 1–3 search-term fields, while also accepting a comma-separated shorthand in the direction field and splitting it automatically.
2. **Background search**: After clicking search, the backend runs automatically with up to 3 keyword groups, cross-query deduplication, and at most 60 full JDs per run. Each completed JD appears as a card streamed into the frontend.
3. **Job cards**: Cards show title, company, salary, location, and a truncated JD; clicking the reading area opens only a validated BOSS link (HTTPS and expected BOSS domains only).
4. **Feedback & learning**: The "Interested / Not interested" buttons on a card **do not trigger navigation**; "not interested" smoothly exits with undo support. After every 5 valid feedbacks the AI updates the current profile's preference, affecting only future results — already-shown cards are never re-ranked.
5. **History & cleanup**: Normal results are retained for 30 days; interested and applied jobs are retained until manual deletion.

### AI URL & Key Configuration

Users only configure two items: the AI service URL and the API Key.

- Enter the AI service URL (an OpenAI-compatible `/v1/chat/completions` endpoint) and API Key in the left settings panel.
- "Test connection" uses an embedded fictional résumé to verify transport, JSON generation, and the candidate-v3 extraction contract; it never reads or sends a saved real résumé.
- The "Fetch" and "Test" buttons report in-progress, success, or retryable failure in place; supplemental top notices dismiss automatically according to severity.
- **The Key must enter the system credential store** (Windows Credential Manager / macOS Keychain / Linux Secret Service, via the `keyring` library) and is **never written in plaintext to SQLite, logs, API responses, or exports**.
- The AI settings endpoint returns only the URL, status, and last error code — never the Key or credential reference.

### Privacy Notice

- Résumé text and AI Key are processed locally and never uploaded to any third party (other than the AI service you configure).
- All résumé reads, AI settings reads, and write operations require a local session token (`X-Boss-Token`).
- Résumé deletion atomically removes the original file, extracted text, content hash, filename, and unconfirmed suggestions.
- No Key or résumé text ever appears in logs, history, exports, or error messages.
- Only HTTPS links on the expected BOSS domain (`zhipin.com`) are opened by the frontend.

### Profile Isolation

- Each new résumé defaults to a new job-seeking profile and **does not inherit the old profile's AI negative preferences**.
- Feedback is bound to the current profile; "not interested" only affects that profile's subsequent results.
- Copying a profile copies only the manually confirmed fields — AI preferences do not migrate.

### Card Interaction

- Cards have a fixed height; JDs are auto-truncated (3-line clamp) to keep the reading flow stable.
- Clicking a card's reading area opens the validated BOSS job link in a **new tab**.
- The "Interested / Not interested" buttons **do not navigate** — they only record feedback.
- After "not interested", the card smoothly exits and an undo bar appears for 5 seconds.

### Feedback Learning

- After every 5 valid feedbacks (interested / not interested; revoked ones don't count), the AI updates the current profile's preference.
- Preference updates only affect **future** search results and card ordering — already-shown cards are never re-ranked.
- AI output is validated by the program; **the AI cannot decide task status or bypass manual filtering**.

### No-Auto-Application Boundary

This project **does not** implement:

- Automatic résumé submission
- Automatically contacting recruiters or sending messages
- Hiring probability prediction

The AI is only responsible for JSON-structured résumé parsing, JD ranking, and preference updates. All task status and application actions are decided by the user.

### Data Retention

| Type | Retention |
|------|-----------|
| Normal results | Auto-cleaned after 30 days |
| Interested jobs | Retained until manual deletion |
| Applied jobs | Retained until manual deletion |

Cleanup never touches the résumé directory or uncontrolled paths, and never deletes saved or applied jobs.

### State Directories

- State database: `~/.career-scout/webui/webui.db`
- Job results: `~/.career-scout/job-result/`
- Résumé files: `~/.career-scout/webui/resumes/`

Set `BOSS_PYTHON` before launch to select a specific Python executable.

### Resume-Driven Two-Layer Filtering (002)

A filtering capability layered on top of the 001 workbench, improving match quality via two-layer verification. Overall flow:

1. **Upload résumé**: The user uploads a TXT / PDF / DOCX résumé (skippable when AI is unavailable).
2. **AI reads and suggests values**: The AI reads the résumé while the program fetches the BOSS filter option enumerations (salary range, experience, degree, company scale, funding stage, industry, city). The AI judges which options can be filled from the résumé and returns suggested values.
3. **User confirmation**: The frontend shows the suggested values; the user may edit or leave them. **User-confirmed values take priority — the AI cannot override.** Any field the AI did not provide and the user did not fill stays empty.
4. **Layer-1 search**: Uses the confirmed conditions to call the BOSS search API and scrape back a batch of jobs, all of which proceed to layer 2. An empty city searches nationwide.
5. **Layer-2 verification**: Each job goes through two checks — hard-rule field verification + AI semantic-similarity judgment. Job discovery uses a fixed four-dimension structured assessment; invalid contracts, invalid evidence references, low confidence, or provider failures route the job to review without inventing default scores.
6. **Partition into temporary match/mismatch zones**: Jobs passing both checks go to the match zone; jobs failing either go to the mismatch zone. The match zone is ordered by scrape order — no similarity sorting. The mismatch zone is shown mixed together, without annotating which field excluded a job.
7. **Mark interested / not-interested**: Any job in the match or mismatch zone can be marked and routed to a persistent zone.

No field is mandatory (including city): fields the user did not select do not participate in layer-1 search or layer-2 hard-rule verification.

#### Two-Layer Verification

- **Layer 1**: Calls the BOSS search API with the confirmed conditions to scrape back jobs.
- **Layer 2**: Each scraped job is checked by hard-rule verification (deterministic program logic) + AI semantic-similarity judgment (a fixed four-dimension structured contract). A job can enter high-match only when hard rules pass, the detail is complete, the AI contract is valid, confidence and dimension gates pass, and evidence is traceable. Contract/reference/provider failures route to review with a safe failure code and never use default scores.

Job discovery preserves `match_score`, `confidence`, evidence counts, and safe failure codes (for example `ai_invalid_output`, `evidence_reference_invalid`, `ai_uncertain`, `ai_network_error`, `snapshot_unavailable`, `hard_rule_unknown`, and `experience_level_conflict`) so a genuinely weak match can be distinguished from an assessment failure. A clear conflict between an entry-level job and substantial candidate experience is programmatically blocked from high or adjacent match.

#### Zone Lifecycle

| Zone | Type | Lifecycle |
|------|------|-----------|
| Match zone | Temporary zone for this run | Cleared when the next run starts |
| Mismatch zone | Temporary zone for this run | Cleared when the next run starts |
| Interested zone | Persistent zone | Unaffected by zone clearing; retained long-term |
| Trash zone | Persistent zone | Unaffected by zone clearing; retained long-term |

#### Interested Zone and Trash Zone

- **Interested zone**: Clicking "Interested" routes a job to the persistent interested zone for long-term review. Cards in the interested zone are clickable and open the corresponding BOSS original job page in the browser — only HTTPS links on the expected BOSS domain (`zhipin.com`) are allowed.
- **Trash zone**: Clicking "Not interested" routes a job to the persistent trash zone, where the list of previously not-interested jobs can be viewed.
- **Display exclusion**: When subsequent search results are displayed, specific jobs previously marked not-interested are excluded and no longer shown. Exclusion **happens only at display time** and does not modify the scraped results; exclusion **identifies specific jobs only** and is not extended to jobs at the same company or with similar characteristics.

#### AI-Unavailable Degradation

When the AI service is unavailable, the system prompts the user and degrades to:

- **Skip résumé**: The résumé upload step can be skipped.
- **Manual filtering**: The filter bar degrades to manual entry; no field is mandatory — leave blank to mean "no limit".
- **Hard-rule-only verification**: Layer 2 performs only hard-rule verification and skips AI semantic-similarity judgment.
- Layer 1 still scrapes normally using the confirmed conditions via the BOSS search API.

#### No-Auto-Application Boundary

This project **does not** implement:

- No automatic résumé submission
- No automatically contacting recruiters or sending messages
- No hiring probability prediction

The AI is only responsible for reading the résumé to suggest filter values (and in the future, structured semantic-similarity output). All task status, partition verdicts, and application actions are decided by program logic and the user. The AI cannot decide task status or bypass program verdicts.

#### State Directories and Testing

- State directories: reuses 001's `~/.career-scout/webui/` (screening runs `screening_runs`/`screening_results` are written to the same `webui.db`; layer-1 scrape output goes to `~/.career-scout/job-result/`).
- Dependencies: reuses 001's existing dependencies; no new third-party libraries.
- Automated tests: `python -m unittest discover -s tests -v`

### Fast Resume-Driven Discovery Closure (005)

Deterministic closure layered on the 004 workbench: an independent `discovery_v2` policy, four-class progress, progressive results, cancel/resume, 12-hour detail reuse, a source circuit breaker and scoped feedback confine "fast resume-driven job recommendation" within verifiable performance and security boundaries. 004 historical runs keep `policy v1`; new 005 runs use `discovery_v2`; migration 015 is additive and never rewrites 001–014.

#### Default user flow

1. **Upload résumé** → 2. **AI analysis (candidate v4) + direction confirmation** → 3. **Run progress (four-class counters + cancel/resume)** → 4. **Progressive results (3-second polling, revision-based no-redraw)** → 5. **Job/direction/judgment-error feedback (declared scope + revocable)**. The root path `/` is the only official frontend entry, covering upload → correction → confirmation → first batch → cancel/resume → feedback end-to-end.

#### Performance and security boundaries (automated gates SC-001–SC-011)

| Gate | Boundary | Automated verification |
|---|---|---|
| SC-003 | 15 details + required assessments ≤ 600 simulated seconds | `tests/test_discovery_performance.py::Sc003DeterministicOrchestrationGateTests` |
| SC-004 | Progress visible within 10 simulated seconds of work-unit completion; refresh preserves counts | `tests/test_discovery_performance.py::Sc004Sc010Sc011PerformanceGateTests` |
| SC-010 | After cancel, reaches `cancelled` terminal state within 30 wall-clock seconds; completed snapshots/assessments/candidates preserved 100% | same as above |
| SC-011 | Resume with identical input identity does not re-fetch details or re-invoke AI | same as above |
| Default detail concurrency | Stays at 1 (policy ceiling 2 only after real small-sample stability evidence) | `webui/source.py` default |
| 12-hour detail reuse | Same job reused within 12h across runs, not re-fetched | `webui/store.py` reuse guard |
| Source circuit breaker | Trips to `source_rate_limited`/`source_verification_required` after consecutive failures, blocks further fetches | `webui/source.py` breaker |
| Feedback scope | `exact_job` / `exact_direction` / `exact_assessment`; revocable; affects only subsequent runs or current visibility, never rewrites history | `webui/store.py` + `webui/discovery.py::apply_feedback_to_next_run` |

#### Run commands and compatibility notes

```bash
# Launch the dedicated BOSS Chrome (first time)
python3 scripts/boss_cdp_raw.py --setup-chrome

# Launch the web workbench
python3 webui/app.py
# Open http://127.0.0.1:5000 in the browser

# Automated tests (no real Chrome/network, fully mocked)
python3 -m unittest discover -s tests -v

# Golden-sample evaluation (SC-003–SC-009 annotation consistency, no real AI)
python3 tests/fixtures/discovery/evaluate.py
```

Compatibility notes:

- 004 historical runs keep `policy v1`; new 005 runs use `discovery_v2`; both policies coexist, neither rewrites the other's history.
- Migration 015 is additive: only new columns and tables, never rewrites 001–014; existing databases can upgrade in place.
- Default detail concurrency is 1; the policy ceiling of 2 is only enabled after real small-sample stability evidence (out of scope for feature 005).
- Feedback affects only the declared scope and subsequent runs; historical profile/confirmation/assessment facts are never rewritten.

## ✨ Features

- Plaintext salary (API mode, bypasses font-based obfuscation)
- Dual JSON / CSV output
- Detail-page JD scraping + skill analysis
- Aggregated summary + copy-paste prompt after scraping
- Incremental writes (no data loss on crash)
- One-shot environment check + persistent isolated Chrome CDP profile
- Multi-dimension filters (scale, funding, salary, experience, degree, industry)
- macOS + Linux support; Windows paths, process parsing, and the Web UI have automated coverage, while real scraping still requires validation against a logged-in local Chrome session

<details>
<summary>🔍 Why not a Selenium / Playwright crawler?</summary>

- Selenium/Playwright spins up a full instrumented browser — it's heavy, has an obvious fingerprint, and is easily flagged by BOSS Zhipin's risk-control / CAPTCHA.
- This tool connects to your own already-logged-in Chrome (via CDP), reusing a real fingerprint and session, and calls the same legitimate search API the page uses. The `salaryDesc` it returns is already plaintext — no need to parse font-obfuscated DOM salaries.
- The result is more stable than traditional DOM-scraping crawlers and harder to flag as automated traffic.

</details>

## Installation

### Option 1: Clone then install locally (recommended)

Because `hermes skills install` may not reach GitHub directly in some environments, clone the repo first and install locally:

```bash
# 1. Clone the repo
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout

# 2. Copy into the Career Scout skills directory
mkdir -p ~/.hermes/skills/data-science/career-scout/scripts
cp SKILL.md ~/.hermes/skills/data-science/career-scout/
cp scripts/boss_cdp_raw.py ~/.hermes/skills/data-science/career-scout/scripts/
cp scripts/job_summary.py ~/.hermes/skills/data-science/career-scout/scripts/
```

### Option 2: One-line curl install

No need to clone the whole repo — download just the files you need:

```bash
mkdir -p ~/.hermes/skills/data-science/career-scout/scripts && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/SKILL.md \
  -o ~/.hermes/skills/data-science/career-scout/SKILL.md && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/scripts/boss_cdp_raw.py \
  -o ~/.hermes/skills/data-science/career-scout/scripts/boss_cdp_raw.py && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/scripts/job_summary.py \
  -o ~/.hermes/skills/data-science/career-scout/scripts/job_summary.py
```

### Option 3: `hermes skills install` (requires direct GitHub access)

```bash
hermes skills install https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/SKILL.md --category data-science
```

> Note: this depends on the hermes process being able to reach GitHub directly. If you hit a timeout or connection failure, use Option 1 or 2.

### Verify the installation

```bash
# Check that the files exist
ls ~/.hermes/skills/data-science/career-scout/SKILL.md
ls ~/.hermes/skills/data-science/career-scout/scripts/boss_cdp_raw.py
ls ~/.hermes/skills/data-science/career-scout/scripts/job_summary.py
```

After installing, just say in a Career Scout conversation: "Search BOSS Zhipin for AI Agent jobs in Shanghai."

## Use as a CLI tool

You don't have to install it as a Skill — use it as a plain CLI:

```bash
# 1. Clone + install deps
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt

# 2. Start Chrome CDP
python3 scripts/boss_cdp_raw.py --setup-chrome
# First run won't copy your main Chrome session; log in to zhipin.com in the dedicated BOSS browser that pops up
# setup waits for login to finish and confirms the API returns plaintext salaries

# 3. Check the environment
python3 scripts/boss_cdp_raw.py --check

# Optional: real browser/API smoke test (writes no result files)
python3 scripts/boss_cdp_raw.py --smoke-test

# 4. Scrape
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis

# 5. Summary + prompt after scraping
python3 scripts/job_summary.py --top 15
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| `--keyword` | Search keyword (default "AI Agent") |
| `--city` | City (Chinese name or code, default Shanghai). **Supports cities nationwide** (300+, incl. tier-3/4/5); city codes auto-sync from BOSS at runtime. See [`data/city_codes.json`](data/city_codes.json), or run `--list-cities` |
| `--list-cities [keyword]` | Print the supported city list, optional keyword filter, e.g. `--list-cities 江` |
| `--pages` | Number of pages (max 10) |
| `--format` | json / csv; csv also exports list and detail CSVs |
| `--detail` | Scrape detail-page JD (on by default) |
| `--no-detail` | Do not scrape detail pages |
| `--analysis` | Analysis report |
| `--merge FILE` | Merge existing data (deduped by job_id) |
| `--allow-dom-fallback` | Allow DOM extraction fallback when the API has no data; off by default, salaries may be unreliable |
| `--check` | Environment check (CDP + deps + login state) |
| `--smoke-test` | Run one real Chrome/CDP BOSS search API smoke test, writes no result files |
| `--setup-chrome` | One-shot launch of Chrome CDP (persistent isolated profile) |
| `--copy-login-state` | Manually import the main Chrome's Local State + cookie-related files into the isolated profile (never copied by default, on first run, or on repeated runs) |
| `--reset-chrome-profile` | Rebuild the dedicated BOSS Chrome profile; clears the login state inside this dedicated browser |
| `--no-wait-login` | With `--setup-chrome`, do not wait for login to finish |
| `--login-timeout` | Seconds to wait for login under `--setup-chrome` (default 300) |
| `--output` | List output path (default `~/.career-scout/job-result/`) |
| `--detail-output` | Detail output path (default `~/.career-scout/job-result/`) |
| `--cdp-port` | CDP port (default 9222) |
| `--scale/--salary/--experience/--degree` | Filters |

## Post-Scrape Summary & Prompt

`scripts/job_summary.py` only reads the already-scraped `boss_jobs_*.json` and `boss_details_*.json`, does simple aggregation, and produces a copy-paste prompt. It never reads your local résumé file, pulls in no PDF dependency, and never scores a person against a job.

```bash
# Read the newest boss_jobs_*.json under the default result dir and auto-match the same-timestamp or newest detail file
python3 scripts/job_summary.py

# Specify list and detail files
python3 scripts/job_summary.py \
  --input ~/.career-scout/job-result/boss_jobs_20260625_1200.json \
  --details ~/.career-scout/job-result/boss_details_20260625_1200.json \
  --top 15

# Only emit the prompt
python3 scripts/job_summary.py --prompt-only
```

After installing the package you can also use the entry command:

```bash
uv run career-summary --top 15
```

The summary covers: salary ranges, experience requirements, degree requirements, regional distribution, top companies, skill tags, frequent JD terms. The prompt asks the model to use these stats to fill in résumé keywords, suggest project-story rewrite directions, and produce an interview-prep checklist — while explicitly instructing it not to fabricate experience.

## File Structure

```
career-scout/
├── SKILL.md              # Career Scout Skill definition
├── README.md             # Chinese docs
├── README.en.md          # English docs
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── scripts/
│   ├── boss_cdp_raw.py   # Main scraping script
│   └── job_summary.py    # Post-scrape summary + prompt
├── webui/
│   ├── app.py            # Flask API + background task orchestration
│   ├── core.py           # Validation + explainable ranking
│   ├── store.py          # SQLite tasks, logs, profiles, search runs, feedback
│   ├── workbench.py      # Keyword selection, dedup, budget, card projection
│   ├── resume.py         # Résumé storage, extraction, path validation, atomic delete
│   ├── ai.py             # AI connection test, credential-store ref, JSON contract validation
│   └── index.html        # Dark job-workbench frontend
└── requirements.txt
```

## How It Works

This is a Chrome-CDP-based BOSS Zhipin crawler. Core flow:

1. Connect to an already-open Chrome via the Chrome DevTools Protocol (CDP)
2. Inject JS inside the BOSS Zhipin page that calls the search API via synchronous XHR
3. The API returns plaintext `salaryDesc`, bypassing the front-end font obfuscation
4. The list API preserves `securityId` / `lid` context, carried into the detail page
5. Each page is written to disk immediately, deduped by `job_id`

DOM extraction is not used for the list by default, since DOM salaries may be hit by font-based obfuscation. Only when `--allow-dom-fallback` is explicitly passed will it fall back to DOM when the API returns no data.

For detail pages, the scraper only extracts a section containing the job-description heading. Full-page `body` text is diagnostic input for detecting login walls and navigation shells and is never written directly as a JD. If the page contains the login-to-view-full-content marker, the crawl fails explicitly and stops before truncated text, recruiter metadata, company sections, or recommended jobs can be saved as a complete JD.

`--input ... --analysis --no-detail` first loads `--detail-output`, then the `boss_details_*.json` with the same timestamp in the same dir as the input list, and finally the newest detail file under `~/.career-scout/job-result`.

## Chrome Profile Security Policy

`--setup-chrome` uses a persistent isolated profile by default — it neither symlinks nor copies your main Chrome data. First launch and subsequent launches only create or reuse this dedicated profile:

- `~/.career-scout/chrome-profile`

Without an explicit `--output` or `--detail-output`, scraping results are saved under:

- `~/.career-scout/job-result`

On first use you must log in to BOSS Zhipin manually inside this dedicated Chrome. `--setup-chrome` waits for the login to finish and uses the search API to confirm it can get plaintext `salaryDesc` before returning. The session is stored inside the dedicated profile and survives reboots; re-running `--setup-chrome` does not wipe it and does not affect your main Chrome, Gmail, GitHub, or other accounts.

If you really need to import the BOSS session from your main Chrome, run explicitly:

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --copy-login-state
```

`--copy-login-state` overwrites the corresponding cookie-related files inside the isolated profile on every run; do not pass this for daily launches. It only copies `Local State` and `Default/Cookies*`, `Default/Network/Cookies*`-style cookie database files — not password stores, history, extensions, or a full profile. To wipe the dedicated browser's login state:

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --reset-chrome-profile
```

## 📌 TODO

- [ ] Strengthen the detail-page `Referer` and request fingerprinting to further reduce risk-control triggers

## License

MIT

## Friends

- [LINUX DO](https://linux.do/) — A sincere, friendly, and vibrant tech community. This project endorses and recommends it.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=czyooutzilas-sketch/career-scout-preview&type=Date)](https://star-history.com/#czyooutzilas-sketch/career-scout-preview&Date)
