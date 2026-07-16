# FIX_PLAN_v0.1 — FREE_PUBLIC_MVP 修正計画

調査日: 2026-07-16
対象commit: 1ed4779 (main, PR #13マージ後)

---

## 調査結果サマリー

指示された8ドキュメント (AGENTS.md, README_HANDOFF.md, docs/00〜05) は全て不在。
代替として IMPLEMENTATION_PLAN.md, docs/current_state_inventory.md, docs/schema_gap.md、
および全実装ファイルを調査済み。

全12問題を確認。以下に各項目の調査結果と修正計画を記載。

---

## Priority A — 実戦判断を誤らせる重大問題

### A1. date_role_split の構造的誤検出

**現在の問題**:
`chain_detector.py:detect_date_role_split()` (L402-486) が `event_family_id` を系列横断比較のキーに使用。
`event_family_id` は店舗固有 (例: `hall_a_7_day` vs `hall_b_7_day`) のため、
同じ意味の「7のつく日」でもIDが異なる。
結果: 各familyが必ず1店舗だけに存在 → `max_share = 1.0` → `mean_concentration = 1.0` → 常にpromoted。

**原因**: `event_families` テーブルに `canonical_family_key` がない。
`build_event_families.py` が店舗ごとに独立したIDを生成し、系列横断で意味統合する仕組みがない。

**変更対象ファイル**:
- `tools/migrate_db.py` — event_families に canonical_family_key 列追加
- `tools/build_event_families.py` — canonical key 生成ロジック追加
- `tools/chain_detector.py` — detect_date_role_split を canonical_family_key ベースに書き換え
- `tests/test_date_role_split_canonical_family.py` — 新規 (DR-01〜DR-05)

**DB migration**: あり (ALTER TABLE event_families ADD COLUMN canonical_family_key TEXT)
**テスト追加**: DR-01〜DR-05 (5件)
**既存互換性**: event_families テーブルに列追加のみ。既存IDは保持。
**rollback**: canonical_family_key 列を無視すれば旧動作に戻る。
**実DB検証**: 7チェイン37店舗で date_role_split を再実行し、promoted 件数が減少することを確認。

---

### A2. 系列パターンの未検出レコードまでUI表示

**現在の問題**:
`chain_detector.py` の各検出器は result dict に `promoted: True/False` を含むが、
`persist_chain_results()` (L668-703) が promoted を DB に保存しない。
`build_site_data.py:enrich_with_v12()` が chain_pattern_results を全件読み込み、
app.js が無条件で表示。confidence 0.0 のレコードも「傾向あり」と同じ見た目。

**原因**: chain_pattern_results テーブルに promoted, status 列がない。

**変更対象ファイル**:
- `tools/migrate_db.py` — chain_pattern_results に promoted, status, subject_key 列追加
- `tools/chain_detector.py` — persist 時に promoted, status を保存
- `tools/build_site_data.py` — promoted=1 AND status='detected' のみ読み込み
- `app.js` — renderChainInfo で promoted フィルタ (バックエンドで絞るが防御的に)
- `tests/test_chain_pattern_promotion_visibility.py` — 新規 (CP-01〜CP-04)

**DB migration**: あり (ALTER TABLE chain_pattern_results ADD COLUMN promoted/status/subject_key)
**テスト追加**: CP-01〜CP-04 (4件)
**既存互換性**: 既存レコードは promoted=0, status='unknown' で保持。表示から消える。
**rollback**: promoted/status 列を無視し全件表示に戻す。
**実DB検証**: 33件中 promoted=1 の件数を確認。

---

### A3. 系列3店舗以上でペア別結果が上書き

**現在の問題**:
`chain_detector.py:build_all_chain_patterns()` (L706-758) が3店舗チェインで
A×B, A×C, B×C の3ペアを生成するが、`persist_chain_results()` が
`INSERT OR REPLACE` で同じ PK `(chain_id, event_family_id, pattern_type, valid_from)` に書き込む。
ペア識別子がないため最後のペア (B×C) だけが残り、A×B, A×C は消失。

**原因**: chain_pattern_results の PK に subject_key (ペア識別子) がない。

**変更対象ファイル**:
- `tools/migrate_db.py` — chain_pattern_results_v2 テーブル作成 (subject_key を PK に含む)
- `tools/chain_detector.py` — persist 時に subject_key を付与 (pair:A|B 形式)
- `tests/test_chain_pattern_pair_primary_key.py` — 新規 (PK-01〜PK-04)

**DB migration**: あり (新テーブル chain_pattern_results_v2 + 旧テーブルからのデータ移行)
**テスト追加**: PK-01〜PK-04 (4件)
**既存互換性**: 旧テーブルは残す。読み込みを v2 に切り替え。
**rollback**: 読み込み先を旧テーブルに戻す。
**実DB検証**: 3店舗以上のチェイン (maruhan:5店, espace:3店, bigdipper:3店) で全ペア保存を確認。

---

### A4. 機種ローテが選抜日ではなく全イベント日を使用

**現在の問題**:
`build_machine_scores.py` (L92-119) の rotation 計算が `same_fam_rows` の全出現日を使用。
`dates_present = [r[0] for r in same_fam_rows]` — 選抜されなかった日も含む。
結果: イベント開催周期を測定しており、機種の選抜周期ではない。

**原因**: `same_fam_rows` のクエリ (L65-86) が `event_selected_label` でフィルタしていない。

**変更対象ファイル**:
- `tools/build_machine_scores.py` — rotation を selected dates ベースに修正。
  selected_dates / all_appearance_dates を分離。
  days_since_last_selected, selected_gap_median, selected_gap_mad, rotation_fit を再計算。
  標本不足時のフォールバック追加。
- `tests/test_machine_rotation_selected_dates.py` — 新規 (ROT-01〜ROT-05)

**DB migration**: なし
**テスト追加**: ROT-01〜ROT-05 (5件)
**既存互換性**: rotation スコアが変わるため予測スコアに影響。frozen run は不変。
**rollback**: 旧クエリに戻す。
**実DB検証**: 戸越銀座 (FULL) で選抜履歴のある機種の rotation_fit を目視確認。

---

### A5. organic モデルへイベント日混入 + model gate 未適用

**現在の問題**:
1. `build_machine_scores.py` (L78-86) の organic path クエリが `machine_days` を全行取得。
   イベント日のデータが organic 特徴量に混入し、通常日投入率が薄まる。
2. `build_machine_labels.py:compute_organic_model_gate()` (L291-328) は存在するが、
   `build_predictions.py` から呼ばれていない。全ホールで organic 予測が生成される。

**原因**: organic クエリにイベント日除外フィルタがない。gate 関数の呼び出し漏れ。

**変更対象ファイル**:
- `tools/build_machine_scores.py` — organic path で event_family_id IS NULL フィルタ追加
- `tools/build_predictions.py` — compute_organic_model_gate() を呼び出し、
  gate 未通過ホールの organic 予測に警告追加 + 標準表示抑制
- `tests/test_organic_normal_days_only.py` — 新規 (ORG-01〜ORG-05)

**DB migration**: なし
**テスト追加**: ORG-01〜ORG-05 (5件)
**既存互換性**: organic 予測スコアが変わる。gate 未通過ホールで organic Top5 非表示。
**rollback**: フィルタ除去 + gate 呼び出し削除。
**実DB検証**: 有効通常日20日未満のホールで organic が disabled になることを確認。

---

## Priority B — 再現性と配信の重大問題

### B6. feature hash が hall_days のみ

**現在の問題**:
`build_predictions.py:build_features()` (L77-102) が `hall_days` テーブルだけをハッシュ。
machine_days, tail_days, event_families, chain_pattern_results, capabilities 等が変わっても
feature_snapshot_hash は不変。凍結 run の再現性証明が不可能。

**原因**: build_features() が hall_days のみクエリ。

**変更対象ファイル**:
- `tools/build_predictions.py` — build_features() を拡張。
  input_hashes manifest 形式で全入力テーブルを個別ハッシュ化。
  最終 feature_snapshot_hash は manifest の canonical hash。
- `tools/prediction_utils.py` — manifest 検証ユーティリティ追加
- `tests/test_feature_snapshot_manifest.py` — 新規 (HASH-01〜HASH-05)

**DB migration**: なし
**テスト追加**: HASH-01〜HASH-05 (5件)
**既存互換性**: feature_snapshot_hash の値が変わる。旧 frozen run は不変。
**rollback**: 旧 build_features() に戻す。
**実DB検証**: machine_days 1行変更で hash が変わることを確認。

---

### B7. frozen run 未指定で v1.2 予測が vault に入らない

**現在の問題**:
`build_site_data.py` の `--frozen-run` を省略すると、`enrich_with_v12()` (L181) の
`if frozen_run_path and frozen_run_path.exists()` が False になり、
v1.2 machine/tail 予測が vault に入らない。通常の更新手順で旧表示だけ更新される。

**原因**: v1.2 予測の vault 注入が optional CLI flag 依存。

**変更対象ファイル**:
- `tools/build_free_public_release.py` — 新規。1コマンドで全パイプライン実行。
  内部で prediction build → freeze → site data build (frozen-run 自動指定) → encrypt → verify。
- `tools/build_site_data.py` — frozen run 自動検出ロジック追加 (predictions/frozen/ から最新有効 run を選択)
- `tests/test_release_build_pipeline.py` — 新規 (REL-01〜REL-05)

**DB migration**: なし
**テスト追加**: REL-01〜REL-05 (5件)
**既存互換性**: 既存 build_site_data.py の引数は維持。
**rollback**: 新コマンドを使わず旧手順に戻す。
**実DB検証**: 1コマンドで vault 生成し run_meta 存在を確認。

---

### B8. v1.2 末尾予測が UI 未接続

**現在の問題**:
`build_site_data.py:enrich_with_v12()` が `v12day.tails` を payload に書き込むが、
`app.js` に `renderV12Tails()` がない。`renderFreeSource()` は legacy `familyData.tails` のみ表示。
v1.2 tail prediction (z_shrunk, grade, confidence, warnings) は dead data。

**原因**: renderV12Tails 関数の実装漏れ。

**変更対象ファイル**:
- `app.js` — renderV12Tails() 追加。v1.2 tail がある場合は優先表示、なければ legacy fallback。
  tail_source 区別 (v1_2_prediction / legacy_summary / unavailable)。
  warnings (tail_days 未接続, 無料検定なし, 標本不足等) を表示。
- `style.css` — v1.2 tail 用スタイル追加
- `tests/test_v12_tail_ui.py` — 新規 (UI-T01〜UI-T04)

**DB migration**: なし
**テスト追加**: UI-T01〜UI-T04 (4件)
**既存互換性**: legacy fallback を維持。
**rollback**: renderV12Tails 呼び出しを削除し legacy のみに戻す。
**実DB検証**: 戸越銀座で v1.2 tail z_shrunk が表示されることを確認。

---

### B9. counter capability の誤判定

**現在の問題**:
`build_capabilities.py` (L55-64) が `machine_days.avg_games IS NOT NULL` で
`counter_metrics_available` を判定。avg_games はゲーム数集計であり、
BB/RB/AT/CZ/初当たりカウンタとは別物。

**原因**: 実際の counter 列 (bb_count, rb_count 等) の存在チェックがない。

**変更対象ファイル**:
- `tools/build_capabilities.py` — counter_metrics_available を実カウンタ列ベースに変更。
  unit_days に bb_count/rb_count/at_count が存在し有効値がある場合のみ true。
  現状 unit_days が 0 行なので全ホール false が正しい。
- `tests/test_counter_capability.py` — 新規 (CAP-C01〜CAP-C04)

**DB migration**: なし
**テスト追加**: CAP-C01〜CAP-C04 (4件)
**既存互換性**: counter_metrics_available が false に変わるホールがある (表示影響なし、現在 UI 未使用)。
**rollback**: 旧判定ロジックに戻す。
**実DB検証**: 66ホール全件で counter=false を確認。

---

## Priority C — 完成判定

### C10. 実DB E2E スキップ解消

**変更対象**: `tests/test_e2e.py`, `tools/validate_release.py` (新規)
**テスト追加**: test_real_db_release_validation.py (新規)
**実DB検証**: 19件全テスト skip=0 で実行。

### C11. v0.1 release smoke test

**変更対象**: `tools/build_free_public_release.py` (B7 で作成)
**検証**: 1コマンド実行 → vault 生成 → decrypt verify → calendar backward compat

### C12. 実装報告書

**変更対象**: `RELEASE_VALIDATION_v0.1.md` (新規)
**内容**: 指示書の最終報告形式に従う。

---

## 実装順序

```
A1 (canonical_family_key) → A2 (promoted/status) → A3 (subject_key/v2)
  → A4 (rotation selected dates) → A5 (organic filter + gate)
  → B6 (feature hash manifest) → B7 (release command)
  → B8 (v1.2 tail UI) → B9 (counter capability)
  → C10 (E2E skip解消) → C11 (smoke test) → C12 (報告書)
```

A1→A3 はすべて chain_pattern_results 関連なので連続実装。
A4, A5 は機種スコアリング関連で独立。
B6〜B9 は再現性・配信の独立修正。

---

## migration 安全性

全 migration は:
- ALTER TABLE ADD COLUMN (既存データ不変)
- CREATE TABLE IF NOT EXISTS (冪等)
- 旧テーブル保持 (chain_pattern_results は v2 と並存)
- rollback = 新列/テーブルを無視

既存 slot_atlas.py, atlas_plus.py への影響: なし (これらは新テーブル/列を参照しない)。
