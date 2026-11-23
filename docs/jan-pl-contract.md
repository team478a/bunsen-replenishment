0.7.3追記: 新しい画面は保存済み全商品共通条件を使い、requirements.csvの用意は不要です。以下のCSVは内部保存/旧互換契約です。生成条件とcommon-settings.csv証跡を原本へ保存し、旧履歴は変更しません。[手順](settings-guide.md)。

0.7.1追記: 新規PDF取込は集計日まで反映済み（内部basis=closing）、翌日から需要計算。始業/終業の選択は廃止。JANマスタのCSV一括登録、未登録JANの予測先行取込は[手順](master-workflow-fixes.md)を参照。未登録JANを含む計算は停止する。

# JAN・物理PL方式のCSV契約と操作

0.5.0-preview.1の新方式。requirements.csvが識別子。旧replenishment.csv方式は旧v2として保持し、旧原本の解釈を変えない。実データ接続完了を示さない。

## CSV（UTF-8、BOM可、列名・順序固定）

|ファイル|列（この順）|
|---|---|
|products.csv|product,name,expiry_type,shelf_life_days,confirmed|
|pallets.csv|warehouse,product,p_per_pl,confirmed|
|inventory.csv|warehouse,product,quantity_p,as_of,basis,source_pdf,source_sha256,checker,confirmed|
|forecast.csv|warehouse,product,date,quantity_p,as_of,order_basis,status|
|requirements.csv|warehouse,product,cycle_days,lead_days,safety_days,coverage_days,confirmed|
|source_evidence.csv（保存証跡）|source_pdf,sha256,content_base64|

productは13桁JAN文字列。JANの重複は停止。同じJANの元マスタ行を足し合わせない。warehouseはKOBE/SUGITO。倉庫一覧で確認された対応と提供された出荷・予測CSVのコード対応が逆転しているため、外部コードの共通変換は行わない。PDFは帳票内の神戸支店/杉戸営業所で判定する。

在庫・指示のquantity_pは0以上の整数。原本証跡付きの[予測CSV取込](forecast-csv-import.md)に限り、予測quantity_pは小数を保持する。p_per_plは物理パレット1枚のP数で正整数。倉庫別・JAN別に両倉庫の行を必須とする。甲・PL＋甲・混載・端数積付は対象外。P数は根拠・保存用で、指示数量は常に整数物理PLに一致させる。

cycle_days/coverage_daysは正整数、lead_days/safety_daysは0以上の整数。日別安全在庫は翌日からsafety_days分の予測。到着からmax(cycle_days,coverage_days)日を対象とし、在庫基準以降の需要も反映。各日末の不足最大値を必要Pとし、倉庫別p_per_plで切上げる。到着前欠品は警告し増量だけで解消したと表示しない。

as_ofはタイムゾーン付きの確認済み基準日時。basisはopening/closing。dateはYYYY-MM-DD。forecast.statusはconfirmed/provisional/imputed。原本証跡付き予測はorder_basis=not_added（大口・注文を別加算しない）を使用する。従来の証跡なし契約はorder_basis=included（注文を予測に含む明示）と整数予測を引き続き要求する。正式発行に未確定予測は使用できない。

confirmedはyesのみ。欠損・未確認を0補完しない。商品期限はbest_before/use_byと正整数shelf_life_days。両方空欄なら期限未整備として補充計算のみ可能、14日計画を停止する。登録JANマスタ画面では全項目の明示確認を求める。

## PDF在庫の自動読取と確認

[PDF読取の操作・検証仕様](pdf-inventory-reader.md)。確認済みの「在庫一覧受払」様式を読み、商品合計だけを採用する。担当者による数量確認・必要な修正と理由の記録後にinventory.csv、source_evidence.csv、products.csv、pallets.csv、pdf-review.jsonを新規保存する。予測・補充条件CSVは別途配置する。

PDF原本のSHA-256、13桁JAN、現在庫ケース・バラ、C入、集計日、作成日時、ページ、空欄の解釈、確認者、変更前後の数量と理由を保存。空欄は未確認のまま保持し、確認済みの帳票ルールを指定した場合だけ0へ換算する。原本表示で左側が切れているJANも内部文字が13桁ある場合はそのまま読み、6桁しかない場合に補完しない。画像PDF・別様式・ページ欠落・商品合計の欠落や重複は停止する。

従来の未確認ひな形生成関数は互換用として維持する。PDF原本証跡・確認済みCSVの取込時照合も維持する。読取だけでconfirmed=yesにはせず、担当者の明示保存操作を必須とする。

## 対象外の明示

transit.csvは存在しても読まない・ハッシュに含めない。入庫予定/輸送中は対象外であり確認済み0件ではない。未確定製造・ブンセン在庫・実ロットも使用しない。内部互換用transit空リストは計算や帳票で0件宣言に使わない。新方式の計算行transit_p/transit_checked_at、14日計画confirmed_inboundはnull。帳票は「対象外」と表示する。

14日計画内で自ら作った補充は、次の計画日の重複補充を避けるため内部の計画入庫として反映する。外部の確定入庫とは区別する。概算期限は保存した計算日＋商品期限日数、16物理PL/便・1PL1JAN。実期限・実出荷や最適性を保証しない。

## 保存・改訂

登録JANマスタは旧登録商品とは別イベントで保存。JAN・品名・期限・両倉庫PL数、変更前後、担当者、根拠、確認、版を記録。入力変更で確認解除。登録版の変更後は再取込・再計算し、確定直前にも版照合する。旧指示と同じ発行番号/改訂機構、バックアップ・閲覧専用復元を利用。原本・計算・修正P・理由・確定数量・対象外方針・PDF証跡を保存する。

## 架空試用

```
BunsenTools.exe jan-kit --dispatch 2026-11-18 --output C:\BunsenTrial\jan-new
BunsenReplenishment.exe --data-dir C:\BunsenTrial\jan-new\home
```

出荷予定日を指定日としjan-new/csvを取り込む。サンプルは神戸必要120P→240P、杉戸必要72P→480P（ともに1物理PL）。すべて架空条件で本番値ではない。固定日付検証はpython -m bunsen.tools jan-demo --output 新規出力先。

## 運用前の不足

在庫基準時刻・始業/終業・空欄ルール、データ別外部倉庫コード対応、めかぶの期限とPL積載数、正式補充設定と予測区分の運用責任。予測CSVは基本P列のみ採用、大口除外、小数保持後の物理PL切上げを実装した。外部コードは確認待ちのため固定しない。添付Excelの専用変換は未実装。確認済み様式以外のPDFや画像OCRには対応せず、未確認値は推定登録しない。
