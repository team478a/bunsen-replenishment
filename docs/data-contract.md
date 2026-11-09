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

輸送中の網羅確認日時は配送先在庫as_ofと一致させる。予測as_ofと在庫as_ofは出荷日以前、将来データは禁止。計算に必要な連続日付は配送先在庫の基準日から到着日＋coverage_days−1まで。欠落日は明示して停止する。確認時刻は取込時にも未来でないことを検証する。現物在庫の当日朝条件は正式発行時に検証する。

本番CSVのコード対応、締時刻、輸送中と在庫の境界、注文内包方式、鮮度の許容時間、商品重量以外の積載条件を合意してから契約を改訂する。ファイル単位の差分追加入力は不可で、常に一式を取り込む。
