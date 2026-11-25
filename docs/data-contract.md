0.7.7追加: 乾麺倉庫の確認済み実在庫CSVは、必要補充計算を変えず、神戸・杉戸の指示数量合計との別照合に使用する。CSV契約・画面・履歴・検証計画は [乾麺倉庫実在庫取込](dry-stock-intake.md) を参照。

# 0.7.6 倉庫別補充条件の内部証跡

保存条件はKOBE/SUGITO別の4項目とする。内部common-settings.csvはwarehouse列付きの2行を新形式とし、旧1行は原本互換用に読み取る。倉庫の欠損・重複・版の不一致、生成したrequirements.csvとの不一致は停止する。外部CSVの倉庫番号は確認済み対応だけを使う。詳細は[倉庫別条件への改修](warehouse-settings.md)。

0.7.0のJAN方式には[予測結果CSV取込](forecast-csv-import.md)を追加。原本証跡付きの変換に限り小数Pとorder_basis=not_addedを使用します。以下の旧契約の整数・includedの意味は変更しません。

最新のJAN・物理PL・入庫対象外方式は [jan-pl-contract.md](jan-pl-contract.md) を参照。以下は旧v2の原本互換契約です。

# 新方式CSV仮契約 v2

実CSV仕様は未確認。以下はアプリ用の仮契約で、接続完了や業務承認を示さない。UTF-8(BOM可)、ヘッダー名・順序を厳密検証。欠損・未確認・不正値を推定/0補完しない。全確認日時はタイムゾーン付きISO8601、対象日はYYYY-MM-DD、数量/日数は0以上の整数。商品換算・cycle_days・coverage_daysは正整数。

## 必須ファイル

replenishment.csvの存在で新方式を識別する。全商品の神戸・杉戸について在庫/設定の一意な組合せが必要。

| ファイル | 列（この順） |
|---|---|
| products.csv | product,name,p_per_ball,balls_per_case,cases_per_pl,ship_unit,confirmed |
| inventory.csv | warehouse,product,quantity_p,as_of,basis,confirmed |
| forecast.csv | warehouse,product,date,quantity_p,as_of,order_basis,status |
| transit.csv | shipment_id,warehouse,product,quantity_p,arrival_date,confirmed |
| replenishment.csv | warehouse,product,cycle_days,lead_days,safety_days,coverage_days,transit_checked_at,confirmed |

products.csvは旧フル列（product,name,p_per_ball,balls_per_case,cases_per_pl,ship_unit,weight_g,min_remaining_days,confirmed）も受け付け、weight_g/min_remaining_daysは新方式で使用・検証しない。商品換算と確認区分は同じ項目を再利用する。

warehouseはKOBE/SUGITO。ship_unitはPL/CASE。confirmedはyesのみ（確認済み実データの宣言）。forecastは予測値で、statusはconfirmed/provisional/imputed。confirmed予測も実績や確定在庫ではない。order_basis=includedは確定注文を既存予測に含む宣言で、注文CSVを別加算しない。

inventory.basis=openingは基準日の始業在庫、closingは基準日の終業在庫。日中途中の在庫は残り時間の予測と対応できないため、そのまま読み替えずデータ責任者が基準を整備する。as_ofは元データの基準/更新日時。基準日の需要を含めるかはbasisで明示する。

transit_checked_atは在庫と同じ基準日時で、在庫未反映の確定入庫を網羅した確認。入庫0件の場合もヘッダーのみのtransit.csvとこの宣言を必須とし、ファイル欠損を0件にしない。shipment_idの重複、未知商品/倉庫、在庫反映済みの日付の入庫は拒否する。未確定予定はconfirmed=yesへ変換しない。

cycle_daysとcoverage_daysの長い方を対象期間とし、safety_daysは各日末に翌日から必要な予測日数。値は設定として保存し、業務上の正式値を推測しない。サンプルは架空例。

## 使用しない入力

新方式でdry_stock.csv、vehicles.csv、warehouses.csv、policies.csvは使用も検証もしない。残置された旧ファイルがあっても計算・確定に影響させず、取込原本/ハッシュにも含めない。それ以外の契約外CSVは黙って無視せず停止する。特定の業務Excelは入力としない。

## 履歴・出力

v2発行payloadはworkflow=2とmeaning=replenishment_instruction_not_shipment_actualを保持。元CSV・正規化ハッシュ・型付きデータ・日時・設定・需要予測・自動量・修正理由・最終量・警告・改訂を保存する。旧DBのテーブルは変えず、旧原本/帳票を保持する。旧v0契約はlegacy-data-contract-v0.mdを参照。

読取中の更新検査は引き続き一覧/属性/バイト列の前後比較を行う。新方式で使わない旧ファイルは監視対象外。原子的取得や同一業務バッチは保証しない。正式発行では保存原本を再読し全検証する。入力が更新されたら担当者が再取込する。

CSVの項目対応表は新方式の全項目を未確認で生成する。実列名・単位・換算・確認者を推測で埋めない。
# 14日計画用の商品期限拡張（2026-11-16）

products.csvは既存ヘッダーに加え、confirmed直前へexpiry_type,shelf_life_daysを追加したヘッダーを受理する（既存フル商品マスタの拡張も可）。expiry_typeはbest_before（賞味期限）/use_by（消費期限）、shelf_life_daysは製造日からの正整数日数。1PLの数量は既存換算数から算出する。

両期限項目とも空欄の行は「期限マスタ未整備」として保持し、既存補充計算のみ利用可能。片方だけ欠損・不正値・ゼロは取込エラー。14日計画には全商品の期限項目が必要で、欠損商品・項目・計算日を示して停止する。管理画面で商品ごとに確認保存し、他商品の不明値は補完しない。

14日分の出荷期間のほか、到着・対象期間・安全在庫評価分の予測が必要。概算期限まで予測が延びていない場合、期限内過大補充の評価は確認不能。正式な実CSV仕様は引き続き未確認。[配送計画仕様](delivery-plan.md)参照。

## 画面登録の商品マスタ（0.4.0）

日常操作で登録マスタ利用を明示選択した場合だけ、確認済みローカル登録の全商品をproducts.csv原本として使用する。物理products.csvは読み取らず、inventory/forecast/transit/replenishmentの4CSVを従来の安定読取・必須検証にかける。登録0件、未対応CSV、登録商品に不足する倉庫別行や予測は停止する。既存CSV方式はそのまま使用可能。

画面登録では商品コード・名称・全換算数・出荷単位・期限種別/日数・担当者・理由・明示確認を必須とする。コードは制御文字/縦棒不可、換算/日数は正整数。1PL当たりPは確認済み換算数の積で求める。入力CSVへ書き戻さず、保存したマスタ版と原本を計算/発行/配送計画に保持する。マスタ更新後の古い版では正式確定できない。[商品マスタ仕様](product-master.md)参照。


2026-11-24 / 0.7.8: 新規PDF取込では、マスタ登録済みでもPDF未掲載のJANは、その倉庫・今回の取込と補充計算から除外する。在庫0やマスタ全体の対象外には変更しない。次回PDFに掲載されれば再び対象。掲載商品の数量空欄は従来どおり未確認。未掲載JAN・倉庫・原本ハッシュをpdf-presence.csvと確認履歴へ保存し、取込・履歴再計算時に原本PDFで検証する。旧証跡のないCSV・履歴の必須在庫検証は変更しない。詳細は[PDF取込仕様](pdf-inventory-reader.md)。


2026-11-24 / 0.7.10: 乾麺在庫Excelの実在庫数は取置差引後の出荷可能パック数。現在はJANで合算し、品番・元行の証跡も保持する。同じJANに未入力行があれば合計未確認とし、0補完・取置の二重控除・現物総数の推測をしない。基準日時は担当者が確認入力。旧CSV履歴は実在庫−取置の意味を維持。任意のExcel取込であり、新LL製造計画への依存は追加しない。詳細は dry-stock-intake.md。


0.7.11: 商品別安全在庫は倉庫別基本条件から安全日数のみ上書きし、別のproduct-safety.csvで版・変更根拠を保存します。全商品一覧は取込マスタ全商品、未掲載は数量未確認。出荷対象一覧は手修正後の指示数量0超のみ。旧原本・旧履歴・旧API出力は維持します。詳細は[新機能仕様・検証計画](product-safety-and-lists.md)。
