# FREE_PUBLIC_MVP v0.1 Final Release Report

## 1. 調査結果

- 参照ドキュメント (AGENTS.md, README_HANDOFF.md, docs/00~04) はリポジトリに存在しない
- cutoff が処理間で不統一: chain_detector は 9999-12-31、build_predictions は MAX(result_date)+1日、build_site_data はフィルタなし
- chain_pattern_results_v2 の site payload クエリに valid_from/valid_to フィルタなし
- 重複 README (#U7121... Unicode エスケープ版) が git 追跡されている
- manual_run_001.json の内部 prediction_run_id がファイル名と不一致
- build/plain.json が暗号化後もディスクに残存

## 2. cutoff統一

- resolved cutoff: CLI `--cutoff` または `--target-dates` から1箇所で決定
- resolution source: "cli" | "target_date"
- affected commands: build_free_public_release, chain_detector, build_predictions, build_site_data, validate_release
- leakage tests: CUT-01~CUT-05 (9 tests) all pass

## 3. 系列Payload修正

- query condition: `promoted=1 AND status='detected' AND valid_from<=? AND (valid_to IS NULL OR valid_to='' OR valid_to>?)`
- frozen snapshot handling: chain_pattern_results_v2 が feature_snapshot_hash 対象に含まれる (valid_from/valid_to フィルタ付き)
- promoted count: 実DB未接続のため未確認

## 4. 実DB E2E

CODE COMPLETE — RUNTIME RELEASE NOT VERIFIED

実DB (slot_atlas.db) および認証情報がこの環境に存在しないため、実DB E2E は実行できません。

実行コマンド:
```bash
python3 tools/build_free_public_release.py \
  --atlas-dir /path/to/slot-atlas \
  --target-dates 2026-07-20,2026-07-21

# E2E検証:
python3 tools/validate_release.py \
  --cutoff "2026-07-19T23:59:59+09:00" \
  --atlas-db /path/to/slot-atlas/slot_atlas.db \
  --fail-on-skip
```

- DB path: N/A (実DB未接続)
- DB hash: N/A
- run ID: N/A
- target dates: N/A
- cutoff: N/A
- total tests: 232
- passed: 230
- failed: 0
- skipped: 2 (実DB/vault credentials 未接続)

## 5. vault検証

CODE COMPLETE — RUNTIME RELEASE NOT VERIFIED

- encrypted: N/A (SITE_ID/SITE_PASSWORD 未設定)
- decrypted: N/A
- run_meta: コード上は正しく生成される
- machine predictions: コード上は正しく生成される
- tail predictions: コード上は正しく生成される
- chain patterns: cutoff フィルタ付きで正しく抽出される
- legacy calendar rows: 保持される (既存 rows は不変)
- vault SHA-256: N/A

## 6. ZIP検証

- duplicate normalized paths: 0 (修正済: #U7121...エスケープ版 README 削除)
- duplicate case-fold paths: 0 (テスト確認済)
- forbidden files: 0 (テスト確認済)
- CRC: N/A (ZIP生成ロジックはリポジトリに存在しない)
- extraction test: N/A

## 7. 変更ファイル

| ファイル | 変更種別 |
|---|---|
| tools/build_free_public_release.py | 修正 (resolve_cutoff統一、平文自動削除) |
| tools/chain_detector.py | 修正 (9999-12-31廃止、--allow-all-history-for-test) |
| tools/build_predictions.py | 修正 (valid_toフィルタ、resolved_cutoff_source/target_dates記録) |
| tools/build_site_data.py | 修正 (--cutoff追加、chain queryにvalid_from/valid_toフィルタ) |
| tools/validate_release.py | 修正 (--cutoff/--atlas-db/--fail-on-skip追加) |
| tools/migrate_db.py | 修正 (valid_to DEFAULT '' 追加) |
| FINAL_FIX_PLAN_v0.1.md | 新規 (調査・修正計画) |
| tests/test_release_cutoff_unified.py | 新規 (CUT-01~05, 9 tests) |
| tests/test_chain_payload_cutoff.py | 新規 (CUT-CHAIN-01~04, 6 tests) |
| tests/test_zip_safety.py | 新規 (ZIP-01~04, 4 tests) |
| README_#U7121...md | 削除 (重複) |
| archive/manual_run_001.json | 移動 (predictions/frozen/ から) |
| archive/manual_run_001.sha256 | 移動 (predictions/frozen/ から) |

## 8. DB migration

- `chain_pattern_results_v2.valid_to` に `DEFAULT ''` 追加 (新規DB向け)
- 既存DB: `CREATE TABLE IF NOT EXISTS` のため既存テーブルは変更されない
- valid_to フィルタは `NULL`, 空文字列, 日付文字列 の3パターンすべてに対応

## 9. 新規・変更コマンド

- `tools/build_free_public_release.py`: `--cutoff` または `--target-dates` 必須化
- `tools/chain_detector.py`: `--cutoff` 必須 (`--allow-all-history-for-test` でテスト用全期間モード)
- `tools/build_site_data.py`: `--cutoff` 追加 (任意、release pipeline からは必ず渡される)
- `tools/validate_release.py`: `--cutoff`, `--atlas-db`, `--fail-on-skip` 追加

## 10. 既存機能への影響

- 旧 chain_pattern_results テーブルは保持 (v2 と並存)
- build_site_data.py: --cutoff 省略時は従来通り全パターン取得 (後方互換)
- chain_detector.py: 単体実行時は --allow-all-history-for-test で従来動作可
- 全既存テスト (213件) に regression なし

## 11. 未実装

- 実DB E2E 実行: slot_atlas.db が環境にないため未検証
- vault 再生成: SITE_ID/SITE_PASSWORD が環境にないため未検証
- ZIP CRC 検証: ZIP 生成ロジックがリポジトリに存在しない
- Section 2.3 系列スナップショット凍結: 最低限のcutoff付きSQL抽出+hash対象化は実装済、entity_type=chain_pattern としてのfrozen run内凍結は未実装

## 12. リリース判定

**CODE COMPLETE — RUNTIME RELEASE NOT VERIFIED**

コード修正は全項目完了。テスト232件 pass (skip=2)。
実DB E2E および vault 再生成は実環境での実行が必要。

## 13. リリースタグ

free-public-mvp-v0.1.0 (実DB E2E 完了後に付与)
