# FINAL_FIX_PLAN_v0.1 — FREE_PUBLIC_MVP v0.1 正式リリース化

実施日: 2026-07-16
対象branch: claude/network-folder-access-vzonpm

---

## 1. 現在のcutoff決定箇所

| 処理 | cutoff取得方法 | デフォルト | 問題 |
|---|---|---|---|
| build_free_public_release.py | CLI `--cutoff` (optional) | None → 各処理に委任 | 統一されていない |
| build_predictions.py | `compute_feature_cutoff()` | MAX(result_date)+1日 T00:00:00+09:00 | 独自計算 |
| chain_detector.py | CLI `--cutoff` | **`9999-12-31`** | sentinel date使用 |
| build_site_data.py | なし | なし | cutoff概念が存在しない |
| build_machine_labels.py | なし | なし | 全行処理 |
| build_capabilities.py | `--as-of` (metadata only) | `datetime.now()` | フィルタに使わない |
| build_event_families.py | なし | なし | 全行処理 |
| validate_release.py | なし | なし | cutoff値の検証なし |

## 2. 各処理へ渡されているcutoff

```
build_free_public_release.py
├── migrate_db.py          → cutoff なし (OK: スキーマ変更のみ)
├── normalize_sources.py   → cutoff なし (OK: 正規化のみ)
├── build_event_families.py → cutoff なし (OK: ルールベース)
├── build_machine_labels.py → cutoff なし (要修正: 全行処理)
├── build_capabilities.py   → cutoff なし (OK: 静的カウント)
├── chain_detector.py       → --cutoff (渡す場合のみ)
│   └── デフォルト: 9999-12-31 ← 要修正
├── build_predictions.py    → --cutoff (渡す場合のみ)
│   └── デフォルト: MAX(result_date)+1日 ← 独自計算
├── build_site_data.py      → cutoff なし ← 要修正
│   └── chain_pattern_results_v2 クエリに valid_from/valid_to フィルタなし
├── encrypt_vault.mjs       → cutoff 不要 (暗号化のみ)
└── validate_release.py     → cutoff 検証なし ← 要修正
```

## 3. 系列結果の valid_from / valid_to

- chain_detector.py: `persist_chain_results()` で `valid_from = cutoff_date`, `valid_to = "9999-12-31"` を固定使用
- build_predictions.py: `build_features()` で `WHERE valid_from < ?` のみ (valid_to フィルタなし)
- build_site_data.py: `WHERE promoted = 1 AND status = 'detected'` のみ (日付フィルタなし)
- compute_chain_signal(): `WHERE valid_from < ? AND promoted = 1` (valid_to フィルタなし)

## 4. site payloadでの系列抽出条件

現在: `promoted = 1 AND status = 'detected'` のみ
必要: `+ valid_from <= :resolved_cutoff AND (valid_to IS NULL OR valid_to > :resolved_cutoff)`

## 5. vault生成経路

```
build_site_data.py → build/plain.json
encrypt_vault.mjs → data/vault.json (backup + rollback あり)
decrypt_vault.mjs → 検証用復号
```
問題: build/plain.json が暗号化後もディスクに残る (7MB)

## 6. 実DB E2Eの実行方法

実DB (slot_atlas.db) は .gitignore で除外、リポジトリに存在しない。
現環境では実DB E2E は実行不可 → CODE COMPLETE / RUNTIME RELEASE NOT VERIFIED

## 7. ZIP内重複ファイルの原因

以下の2ファイルが同一内容 (MD5一致) でgit追跡されている:
- `README_無料ソース予測.md` (UTF-8正規名)
- `README_#U7121#U6599#U30bd#U30fc#U30b9#U4e88#U6e2c.md` (Unicodeエスケープ名)

原因: 別セッションでUnicodeエスケープ名でコピーされた

## 8. frozen run名称不整合

`predictions/frozen/manual_run_001.json` の内部 prediction_run_id は `run_20260716_121819`。
ファイル名と内部IDが不一致。

---

## 修正対象ファイル

| ファイル | 修正内容 |
|---|---|
| tools/build_free_public_release.py | resolved_cutoff 統一、全サブコマンドへ伝搬、target-dates 必須化、平文削除 |
| tools/build_predictions.py | cutoff必須化 (release mode)、resolved_cutoff_source 記録 |
| tools/chain_detector.py | 9999-12-31 デフォルト廃止、cutoff必須化 (release mode) |
| tools/build_site_data.py | --cutoff 追加、chain query に valid_from/valid_to フィルタ |
| tools/build_machine_scores.py | cutoff パラメータ明示化 (既に受け取っている、変更最小) |
| tools/validate_release.py | --cutoff 検証追加、--atlas-db/--fail-on-skip 対応 |
| predictions/frozen/manual_run_001.json | archive/ へ移動 |
| README_#U7121...md | 削除 (重複) |

## テスト追加内容

| テストファイル | 内容 |
|---|---|
| test_release_cutoff_unified.py | CUT-01~05: cutoff統一テスト |
| test_chain_payload_cutoff.py | CUT-CHAIN-01~04: 系列payload cutoffフィルタ |
| test_zip_safety.py | ZIP-01~04: Unicode重複、平文非同梱 |

## rollback方法

- cutoff統一: --cutoff 引数を削除し、各処理のデフォルトに戻す
- chain payload: valid_from/valid_to 条件を WHERE から削除
- ZIP整理: git checkout でファイル復元
- frozen run: archive/ から戻す
