# RELEASE_VALIDATION_v0.1 — FREE_PUBLIC_MVP v0.1 最終修正検証

実施日: 2026-07-16  
対象: `Tenjikai-main 3.zip` への直接修正

## 判定

**コード修正: PASS**  
**同梱外の実運用DBを使う最終リリース実行: 未実施**

実運用の `slot_atlas.db` は今回のZIPに含まれていないため、実店舗データでのvault再生成は行っていない。代わりに、無料公開データを模した合成Atlas環境を生成し、以下の1コマンド経路を完走させた。

```text
migration
→ normalization
→ event family
→ labels
→ capabilities
→ chain detector
→ prediction build
→ freeze + DB registration
→ site payload
→ run_meta / machine / tail fields verification
```

## 自動テスト

```text
213 tests passed
19 tests skipped
0 failures
```

19件は、リポジトリ外の実運用 `slot_atlas.db` を要求する既存E2Eテスト。スキップ件数を過少記載していた旧報告を訂正した。

追加確認:

- Python全ツールの構文検査: PASS
- `app.js`構文検査: PASS
- 合成Atlas 1コマンドE2E: PASS
- frozen runのDB登録: PASS
- `run_meta`のsite payload搭載: PASS
- v1.2機種予測のsite payload搭載: PASS
- v1.2末尾の`grade / z_shrunk / n_eff / confidence`保持: PASS
- `local_only`方針: 維持

## 今回の修正

### 1. date_role_splitを強度ベースへ変更

旧実装はイベント登録件数の偏りを見ていたため、出玉の役割分担ではなかった。

新実装:

- 店舗ごとの日次差枚を店舗内標準化
- 同じ`canonical_family_key`を2店舗以上で比較
- 各店舗3日以上の観測を要求
- familyごとの勝者、softmax集中度、上位差を算出
- 2つ以上のfamilyで、2店舗以上へ勝者が分かれた場合のみ昇格
- 登録件数だけの偏りでは昇格しない

### 2. 系列表示の二重防御

- DB取得時: `promoted=1 AND status='detected'`
- Payload: `promoted / status / subject_key / warnings`を保持
- UI: `p.promoted === true && p.status === 'detected'`のみ表示

### 3. 末尾のモデル判定を正本化

Payloadへ以下を保持:

```text
grade
z_shrunk
n_eff
confidence
rank
source
```

UIはscoreからS/A等を再計算せず、モデルが決めた`grade`を表示する。日付こじつけ降格がUIで再昇格する問題を解消。

### 4. releaseコマンド修正

`freeze_run.py`のdraftは位置引数で渡し、`--db`でfrozen runをDB登録する。

```text
python freeze_run.py build/run_draft.json --db slot_atlas.db
```

暗号化時は旧vaultを一時バックアップし、暗号化後に独立した復号検証を実施。失敗時は旧vaultを復元する。

### 5. feature hash拡張

予測へ影響する以下をmanifestへ追加:

- machine labels
- organic active / selected
- q_machine / coverage / positive_rate
- event family rule / canonical key
- chain pattern results
- capability全項目
- organic model gate結果
- 機種・ラベル・末尾・系列の係数と閾値
- `unit_distribution_policy=local_only`

DB schema差がある場合も、存在する列を決定論的に抽出する。

### 6. unknownを負例にしない

- 機種選抜率の分母はラベルが明示的に0/1の行だけ
- NULLラベルはeligibleへ入れない
- coverage / q_machine等が欠損する機種は0ラベルにせずNULLを維持
- ローテと直前選抜も既知ラベルだけで計算

### 7. capability / DB互換性

- `source_snapshots`がない旧DBでもcapability生成が落ちない
- counter capabilityは実counter列だけで判定
- 無料ソースDB読込は各テーブルを独立処理
- optional列欠損でmachine/tail全体が消える問題を修正

### 8. frozen run自動選択

ファイル名順ではなく、run内の`built_at`と`prediction_run_id`を検査して決定論的に選択する。

### 9. cutoff初期値

デフォルトcutoffを最新結果日の翌日00:00に変更。`result_date < cutoff_date`で最新完了日が除外される問題を解消。

## Phase 1.75

無料公開unit dataがない店舗では、引き続き台番予測を生成しない。

```text
unit_daily_available=false
無料公開の台番日次データなし
```

架空のQ_unitや台番号推定は追加していない。

## 実運用環境での最終コマンド

```bash
SITE_ID=... SITE_PASSWORD=... \
python3 tools/build_free_public_release.py \
  --atlas-dir /path/to/slot-atlas \
  --target-dates YYYY-MM-DD,YYYY-MM-DD
```

完走後、以下を確認すること。

```text
build/plain.json.free_source.run_meta
v1_2 machine predictions
v1_2 tail grade/z_shrunk
vault decrypt verification
旧カレンダー行数
```

## 同梱vaultについて

`data/vault.json`は、実運用DBがZIPに含まれていないため再生成していない。コードとパイプラインの修正のみを含む。
