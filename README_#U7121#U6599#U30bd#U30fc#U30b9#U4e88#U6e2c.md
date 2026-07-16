# 無料ソース配置予測 — 接続仕様

この版のTenjikaiは、従来の「店×日」カレンダーに加えて、Slot Atlas側にある機種・末尾・位置データを更新ビルド時に読み込みます。表示データは従来どおり `build/plain.json` にまとめた後、`data/vault.json` へ暗号化されます。

## 画面に追加されるもの

- 選択日の全店舗候補リスト（相対首位以外の店にも切替可能）
- データ層: **FULL / SUMMARY / NONE**
- 全台系度（同族日のうち選抜機種が出た日の割合）
- 機種選抜型（ローテ型 / 再登場型 / 混合型）
- 機種候補度（未校正スコア。確率ではない）
- 末尾候補（平均差枚を末尾間で標準化したz値）
- 設定配置パターン台帳15型
- `unit_days` がある場合の並び・散らし・オセロ・固定位置・前回除外・凹み上げ・据え置きの補助検定

## データ層

| 層 | 条件 | 表示 |
|---|---|---|
| FULL | `machine_days` が5日以上、かつ `tail_days` が3日以上または10行以上 | 機種・末尾・台帳を日タイプ別に表示 |
| SUMMARY | 機種参考スコア、位置シグナル、または片側の日次だけ存在 | 出所警告付き参考表示 |
| NONE | 対応するデータなし | 未接続項目を明示 |

SUMMARYの `machine_scores` は、媒体が結果後に選んだ好調箇所を含み得ます。着席前確率としては扱いません。

## 自動検出するファイル

`--atlas-dir` 配下の `seed/`、`exports/`、`data/`、`build/` を探索します。CSVとJSONに対応します。

- `machine_days.json` / `.csv`
- `tail_days.json` / `.csv`
- `machine_scores.json` / `.csv`
- `position_signals.json` / `.csv`
- `unit_days.json` / `.csv`

ファイル名に上記の語が含まれていれば候補になります。複数ファイルがある場合は結合します。

## 推奨列契約

### machine_days

| 必須 | 列 |
|---|---|
| 必須 | `hall_id`, `date`, `machine_name`, `avg_diff` |
| 任意 | `units`, `avg_games`, `special_selected`, `event_type`, `source` |

`special_selected` がない場合は、暫定的に「平均差枚+1,000枚以上、平均Gがある場合は2,500G以上、台数がある場合は2台以上」を選抜機種とします。Atlas本体で判定済みなら `special_selected` を渡す方が正確です。

### tail_days

| 必須 | 列 |
|---|---|
| 必須 | `hall_id`, `tail` |
| どちらか | `avg_diff` または `z` |
| 推奨 | `date`, `event_type`, `n`, `source` |

日次 `avg_diff` があれば、日タイプ内で末尾ごとの平均を取り、末尾間のz値を計算します。集計済みの `z` だけでも表示できます。

### machine_scores

`hall_id`, `machine_name`, `score`（または `avg_diff`）。FULL用ではなくSUMMARY用です。

### position_signals

`hall_id`, `pattern_type`。任意で `date`, `detail`, `tail`, `z`, `source`。

### unit_days

`hall_id`, `date`, `unit_no`, `diff`。任意で `machine_name`, `games`, `event_type`。

台番日次が入ると、並び等の検定が自動的に有効になります。サイトセブンに限らず、同等の台番×日次データなら接続できます。

## ビルド

Slot Atlasの365日予測を含めて通常どおり再生成する場合:

```bash
python3 tools/build_site_data.py --atlas-dir /path/to/slot-atlas
SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
```

現在のvaultに入っている予測行を保持し、無料ソース分析だけを接続する場合:

```bash
SITE_ID=... SITE_PASSWORD=... node tools/decrypt_vault.mjs
python3 tools/build_site_data.py \
  --atlas-dir /path/to/slot-atlas \
  --base-plain build/plain.json
SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
```

復号スクリプトはID・パスワードをファイルへ保存しません。`build/plain.json` は平文であるため、コミットせず、共有PCでは再暗号化後に削除してください。

実行ログにテーブル行数と `FULL / SUMMARY / NONE` の店舗数が出ます。

無料ソース分析を一時的に外して従来形式だけを作る場合:

```bash
python3 tools/build_site_data.py --atlas-dir /path/to/slot-atlas --no-free-source
```

## 日タイプのまとめ方

表示対象日の `reason` を優先し、次の単位へまとめます。

- 通常 / 平常 / ベース → `通常`
- 周年 → `周年`
- 月=日 → `月=日`
- ゾロ目 → `ゾロ目`
- Nのつく日、20日等 → 日付末尾別（例: `0のつく日`）
- 対応する同族系列がない場合 → `全日参考`（通常日と断定しない）

機種・末尾側に `event_type` / `day_type` / `family` があれば、その値を優先して同じ規則で正規化します。

## 15型の扱い

- 無料の機種日次: #1 全台系、#11 機種ローテ、#15 新台時期
- 無料の末尾集計: #3 末尾
- 複数店の機種日次: #8 合同
- 台番日次: #2 並び、#4 ゾロ目台番、#6 散らし、#7 オセロ、#9 固定位置、#10 前回除外、#12 凹み上げ、#13 据え置き
- 現地レイアウト: #5 角・カドN
- 朝一観測: #14 リセ恩恵配布

台番日次の検定は「兆候」を出す補助指標です。島境界を持たないため列またぎや物理的な角位置は断定しません。
