# CSVデータ契約 v0（仮）

**既存CSVの仕様は未確認。実システムとの接続完了を意味しない。** UTF-8（BOM可）、ヘッダー必須、日付YYYY-MM-DD、日時ISO8601（タイムゾーン必須、JST運用）、整数は0以上。空欄・未確認・重複キー・不正日付・未知商品／倉庫は不備として表示。数量0は明示された確認済みの0として有効。空欄と区別する。確認済みの行は`confirmed=yes`、それ以外は使用不可。数値の推測補完をしない。

| ファイル | 必須列 | 一意キー／意味 |
|---|---|---|
| products.csv | product,name,p_per_ball,balls_per_case,cases_per_pl,ship_unit,weight_g,min_remaining_days,confirmed | product。ship_unitはPLまたはCASE。係数・重量は正整数 |
| warehouses.csv | warehouse,lead_days,confirmed | warehouse。KOBE/SUGITO両方必須 |
| policies.csv | warehouse,product,coverage_days,safety_p,rounding,expiry_basis,transit_checked_at,confirmed | warehouse+product。全組合せ必須。rounding=ceil/floor、expiry_basis=shipment/arrival。輸送中全件確認日時必須 |
| inventory.csv | warehouse,product,quantity_p,as_of,confirmed | warehouse+product。全組合せ必須。as_ofは当該日の需要控除前の始業在庫 |
| forecast.csv | warehouse,product,date,quantity_p,as_of,order_basis | warehouse+product+date。order_basis=includedのみ。quantity_pは予測P。as_ofは予測作成日時 |
| transit.csv | shipment_id,warehouse,product,quantity_p,arrival_date,confirmed | shipment_id。ヘッダーのみは、各policyのtransit_checked_atによって確認済み0件を意味する |
| vehicles.csv | warehouse,product,capacity_g,confirmed | warehouse+product専用便。全組合せ必須。0は出荷不可 |
| dry_stock.csv | product,lot,quantity_p,best_before,checked_at,checker,source,confirmed | product+lot。source=physicalのみ。正式発行は出荷当日の朝（00:00〜11:59 JST）、未来時刻不可、確認者必須 |

products以外の未定義列もエラーとし、誤った単位や注文列の黙認を防ぐ。dry_stock.csvだけは仮指示時に省略可。確認済み0在庫は数量0のロット行で表す。正式発行時は全商品で行が必要。朝在庫はロット網羅の申告値であり、CSVだけで現物確認の真正性は証明できない。確認者が全ロットを記録する責任を持つ。

取込フォルダー直下に契約外のCSVがあれば、拡張子の大小文字を問わず取込全体を停止し、ファイル名を表示する。正式ファイル名は上表の小文字表記に固定する。注文・製造予定のCSVを黙って無視して取込完了とは表示しない。CSV以外のメモ等は読み込まない。

輸送中の網羅確認日時は配送先在庫as_ofと一致させる。予測as_ofと在庫as_ofは出荷日以前、将来データは禁止。計算に必要な連続日付は配送先在庫の基準日から到着日＋coverage_days−1まで。欠落日は明示して停止する。確認時刻は取込時にも未来でないことを検証する。現物在庫の当日朝条件は正式発行時に検証する。

本番CSVのコード対応、締時刻、輸送中と在庫の境界、注文内包方式、鮮度の許容時間、商品重量以外の積載条件を合意してから契約を改訂する。ファイル単位の差分追加入力は不可で、常に一式を取り込む。

## 第3段階のロット分割と履歴形式

CSV列は変更しない。dry_stock.csvには商品・ロットの組合せごとに現物数量・期限・確認者を記録する。ロット番号は異なる商品で同じ文字列を使用でき、在庫は商品別に区別する。

SQLiteの既存JSONスナップショットでは、明細の`quantity_p`を指示総量として維持し、複数ロット指定時のみ`allocations: [{"lot": "L001", "quantity_p": 72}, ...]`を追加する。この場合、旧`lot`欄は空とし、単一ロット形式と併用しない。各割当のキーはlotとquantity_pのみ。数値の空欄・文字列・負数・ゼロ・真偽値を拒否し、正式発行では合計一致・甲単位・期限・在庫を検証する。

`allocations`のない既存明細は従来のlot＋quantity_pとして読む。空の割当を指示総量0へ補完しない。DBテーブル変更・旧履歴の書換えは行わない。

## 第4段階の再開元情報

CSV列・DBテーブルは維持する。再開した仮指示から発行するJSONスナップショットには任意の`source_draft`を追加する。元番号・元データ識別・元発行日時・現在データ識別・変更された入力区分・倉庫商品別の数量／到着日／ロットの比較を記録する。この項目のない既存履歴も再開・再出力できる。

再開対象は同じ出荷日の最新仮改訂に限定する。商品・倉庫のキーが変わった場合や、再開原本のハッシュが一致しない場合は停止する。朝CSVの欠損を前日の原本で補わず、一式を再検証する。再開・照合・発行はそれぞれイベントへ記録し、元の発行スナップショットは書き換えない。

## 第7段階の構造化診断

通常取込と診断は同じCSV検証を使用する。1行の全項目を確認し、不備にファイル・物理終端行・商品・倉庫・対象日／基準日時・ロット・項目・コード・理由・記録値・検証区分を付ける。不備がある入力はDatasetを生成せず停止する。診断側で値を補正したり、既定の数量・単位・確認済み区分を加えたりしない。

必要予測日の連続欠落は先頭日と終端日でまとめる。日付計算の範囲を超える配送日数・補充期間は対象設定の不備として停止する。正常データの補充数量式とCSV列は変更しない。既存の原文正規化・取込ハッシュも維持する。

実CSV対応は未確認のまま。`csv-mapping-review.md`とJSONひな形はレビュー用で、自動変換設定ではない。診断の範囲・未実施項目は`csv-diagnostics.md`参照。

## 第8段階の受入キット

CSV列・数量・換算は変更しない。同梱架空CSVのみ、DATE/TIMESTAMP項目を指定試験日への同一日数差で移す。実CSVはこの変換の入力にできない。kit.jsonに版・元日付・移動日数・元/生成ハッシュを記録する。

試用DBはtrial-info.jsonで識別し、trial_modeイベントと発行payloadのtrial_only=trueを保存する。既存の業務DBスキーマは変更しない。受入記録は全件not_performed、承認unconfirmedで生成し、自動検証結果とは区別する。
