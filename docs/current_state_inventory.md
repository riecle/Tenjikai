# Current State Inventory

Investigation date: 2026-07-16

## 1. Repository Structure

```
Tenjikai/
├── .github/workflows/pages.yml   # GitHub Pages deploy (no build step)
├── .gitignore                    # __pycache__, *.pyc, *.db, build/, slot-atlas/
├── .nojekyll
├── CHANGELOG_2026-07-16.md
├── README.md
├── README_無料ソース予測.md
├── app.js                        # SPA: login, decrypt, calendar, free-source UI (567 lines)
├── data/vault.json               # AES-GCM encrypted payload (~9.8 MB)
├── icons/                        # PWA icons (192, 512, apple-touch)
├── index.html                    # Shell with CSP meta tag
├── manifest.webmanifest          # PWA manifest
├── robots.txt                    # Disallow: /
├── style.css                     # Dark theme, responsive (180 lines)
├── sw.js                         # Service worker: shell atomic + vault best-effort
├── tests/
│   └── test_free_source_predictor.py   # 2 test classes (97 lines)
└── tools/
    ├── build_site_data.py        # CSV/DB → plain.json (163 lines)
    ├── decrypt_vault.mjs         # vault.json → build/plain.json (81 lines)
    ├── encrypt_vault.mjs         # build/plain.json → data/vault.json (100 lines)
    └── free_source_predictor.py  # Machine/tail/pattern analysis (999 lines)
```

External dependency (not in repo, git-ignored):

```
slot-atlas/                       # The upstream prediction engine
├── slot_atlas.py                 # Main engine (754 lines)
├── atlas_plus.py                 # Extensions (577 lines)
├── schema.sql                    # DB schema (234 lines)
├── slot_atlas.db                 # SQLite DB (15.5 MB)
├── seed/                         # halls.json, rules.json, validation_queue.json, CSVs
├── exports/                      # forecast_candidates_365.csv
└── tests/                        # test_slot_atlas.py, test_merge_integrity.py
```

## 2. Language and Dependency Constraints

- **Python**: stdlib-only. Zero third-party packages. No requirements.txt, setup.py, or pyproject.toml.
- **Node.js**: Built-in modules only (node:crypto, node:fs/promises, node:path, node:url). No package.json.
- **Frontend**: Vanilla JS, no frameworks. Strict CSP: `default-src 'self'; script-src 'self'; style-src 'self'`.

## 3. Database (slot_atlas.db)

SQLite, 15.5 MB, 15 user tables.

### Core tables with data

| Table | Rows | Purpose |
|---|---|---|
| halls | 66 | Hall master (market, active, forecast_enabled, travel, exchange, reset_policy) |
| evidence_rules | 410 | Date-pattern rules with match_json, mean_diff, sample_n, status |
| hall_days | 5,306 | Daily hall-level results (avg_diff, total_diff, avg_games) |
| machine_days | 20,229 | Daily per-machine results (avg_diff, units, selected_flag) |
| tail_days | 2,475 | Daily per-tail-digit results (avg_diff, avg_games) |
| machine_scores | 617 | Composite machine scores per hall |
| position_signals | 115 | Published event-specific machine-block signals |
| calendar_flags | 132 | Special date flags (holiday, pre-registered events) |
| source_snapshots | 300 | Fetch metadata (URL, HTTP status, content SHA-256) |
| predictions | 24,090 | 365-day forecasts (run_id=1, one model_run) |
| model_runs | 1 | Single forecast run record |
| validation_log | 245 | Pending validation claims from validation_queue.json |

### Empty tables (schema exists, no data)

| Table | Purpose |
|---|---|
| unit_days | Per-unit daily results (requires paid/field data) |
| regime_changes | CUSUM changepoint detections |
| habit_vectors | 7-dimensional operating-habit vectors |

## 4. Prediction Generation (slot_atlas.py)

### Commands

- `python3 slot_atlas.py init` — Rebuild DB from seed files
- `python3 slot_atlas.py import-hall-days <csv>` — Import daily observations
- `python3 slot_atlas.py generate` — Produce 365-day forecasts

### Forecast logic (forecast_one)

1. Match evidence_rules by date pattern (match_json) and validity window
2. Select primary rule (highest priority × sample_n)
3. Bayesian shrinkage: `shrunk = (mean * n + prior * 4) / (n + 4)`
4. Confidence: `evidence = n / (n + 8)` × `freshness = exp(-age / 60)`
5. Edge: `predicted_mean - decision_floor`
6. Utility edge: `edge - travel_penalty`
7. Rank: S/A/B/C/NO BET based on utility_edge and confidence thresholds
8. Context downgrades: weekend, holiday, long-break, staleness, horizon

### Output

- `predictions` table (24,090 rows)
- `exports/forecast_candidates_365.csv`
- `exports/calendar_365.csv` and `.json`

## 5. Atlas Plus (atlas_plus.py)

Extension commands:

- `habit-vector` — 7-dim hall operating vectors (zero_sum_r, event_compliance, etc.)
- `changepoint` — CUSUM regime detection
- `machine-lookup` — Reverse machine search across halls
- `import-unit-days` — Import per-unit CSV data
- `set-reset` — Set hall reset policy
- `stale-check` — Flag stale data
- `position-tests` — Unit-level statistical tests (hot units, adjacency, tail cells)

## 6. Free Source Predictor (free_source_predictor.py)

### Data sources

Discovers tables from CSV/JSON files in seed/, exports/, data/, build/ directories and from SQLite DB:

- machine_days (20,229 rows)
- tail_days (2,475 rows)
- machine_scores (617 rows)
- position_signals (115 rows)
- unit_days (0 rows, policy-excluded from vault)

### Layer assignment

- **FULL**: machine_dates >= 5 AND (tail_dates >= 3 OR tail_rows >= 10)
- **SUMMARY**: Any data in machine_days/machine_scores/tail_days/position_signals
- **NONE**: No relevant data

### Analysis per hall

- **Machine rotation**: Frequency/strength/positive_rate scoring with Laplace smoothing. Labels: ローテ型/再登場型/混合型
- **Tail z-scores**: Per-tail average diff and z-score. Grades: double-circle/circle/triangle/dash
- **15-pattern catalog**: Geometry (all-machine, adjacent, tail, repdigit, corner, scatter, othello, joint) and operational (fixed, prev-exclusion, rotation, dent-recovery, carry-over, reset-benefit, new-machine)
- **Unit diagnostics**: Pearson phi for adjacency, Jaccard for fixed positions (requires unit_days)

### Family grouping

The `family_key()` function maps evidence_rule labels to families (e.g., "0のつく日", "ゾロ目", "7の日", "通常"). Used to group machine_days for rotation analysis.

## 7. Build Pipeline

```
slot-atlas/                          (external, git-ignored)
  ↓ python3 tools/build_site_data.py --atlas-dir ../slot-atlas
build/plain.json                     (git-ignored)
  ↓ SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
data/vault.json                      (committed, ~9.8 MB)
  ↓ git push → GitHub Actions
GitHub Pages                         (static deploy, no build step)
  ↓ Browser loads app.js
Login → PBKDF2 → AES-GCM decrypt → Calendar UI
```

### Vault format

```json
{"v":1, "kdf":"PBKDF2-SHA256", "iterations":600000,
 "salt":"<b64>", "iv":"<b64>", "ct":"<b64>"}
```

- Salt reuse by default (preserves cached login keys)
- `ROTATE_KDF_SALT=1` forces new salt
- Self-check: decrypt round-trip before writing
- Never overwrites vault if self-check fails

### plain.json structure

```json
{
  "meta": { "model_version", "as_of", "date_range", "hall_count", ... },
  "rows": [
    { "d": "date", "id": "hall_id", "h": "name", "m": "market",
      "r": "S|A|B|C|NO BET", "p": predicted_mean, "e": edge,
      "u": utility_edge, "c": confidence, "n": sample_n, ... }
  ],
  "free_source": {
    "v": 1, "pattern_catalog": [...],
    "halls": {
      "<hall_id>": {
        "layer": "FULL|SUMMARY|NONE",
        "families": { "<key>": { "machine": {...}, "tails": [...], "patterns": [...] } },
        "summary_machines": [...], "warnings": [...]
      }
    }
  }
}
```

## 8. Vault Encryption / Verification

- **Encrypt**: `tools/encrypt_vault.mjs` — PBKDF2-SHA256 (600K iter) + AES-256-GCM
- **Decrypt**: `tools/decrypt_vault.mjs` — Reverse for incremental updates
- Credentials via env vars (`SITE_ID`, `SITE_PASSWORD`), never stored in code
- Self-check before write; output file mode 0o600

## 9. Tests

One test file: `tests/test_free_source_predictor.py` (97 lines, 2 classes)

- `FreeSourcePredictorTest.test_full_summary_none_and_rotation`: Synthetic data → verifies FULL/SUMMARY/NONE layer assignment and rotation detection
- `UnitPolicyGateTest.test_unit_days_excluded_by_default`: Verifies include_unit=False policy

External (slot-atlas): `test_slot_atlas.py`, `test_merge_integrity.py` — test DB seeding, hall counts, validation queue correctness.

## 10. Validation Queue

245 entries in `seed/validation_queue.json`, loaded to `validation_log` table.

Each entry: `target_date`, `hall_id`, `claim` (free-text assertion), `threshold` (confirm_if/kill_if/reframe_if conditions).

All entries have `verdict='pending'`, `run_id=NULL`. No automated evaluation pipeline exists — verdicts require manual or future automated assessment.

## 11. Source Lineage

Minimal. `source_snapshots` table has 300 rows with `source_url`, `content_sha256`, `parse_status`, but:

- No `acquisition_method` field
- No `raw_path` for original files
- No `parser_version` tracking
- No `raw_sources` table as specified in v1.2 contracts
- No revision tracking

## 12. Model Version Management

- `MODEL_VERSION = "slot-atlas-0.11.29"` in slot_atlas.py
- Stored in `model_runs.model_version` (single row)
- Extracted by build_site_data.py via regex
- Displayed in app.js footer
- Stamped into sw.js cache name
- No `config_version` exists anywhere

## 13. Security

- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'`
- `<meta name="robots" content="noindex, nofollow, noarchive">`
- `<meta name="referrer" content="no-referrer">`
- robots.txt: `Disallow: /`
- Login lockout: exponential backoff after 3 failed attempts (2^n seconds, cap 30s)
- XSS prevention: `escapeHtml()` for all user-facing text
- No credentials in code or repo

## 14. Deployment

GitHub Actions: push to main → checkout → upload artifact (repo root) → deploy to Pages.
No build step in CI. All build/encrypt is local before commit.
