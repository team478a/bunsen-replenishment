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

productは13桁JAN文字列。JANの重複は停止。同じJANの元マスタ行を足し合わせない。warehouseはKOBE/SUGITO。外部70301/70302の対応は未確定のため自動変換しない。

quantity_pは0以上の整数。p_per_plは物理パレット1枚のP数で正整数。倉庫別・JAN別に両倉庫の行を必須とする。甲・PL＋甲・混載・端数積付は対象外。P数は根拠・保存用で、指示数量は常に整数物理PLに一致させる。

cycle_days/coverage_daysは正整数、lead_days/safety_daysは0以上の整数。日別安全在庫は翌日からsafety_days分の予測。到着からmax(cycle_days,coverage_days)日を対象とし、在庫基準以降の需要も反映。各日末の不足最大値を必要Pとし、倉庫別p_per_plで切上げる。到着前欠品は警告し増量だけで解消したと表示しない。

as_ofはタイムゾーン付きの確認済み基準日時。basisはopening/closing。dateはYYYY-MM-DD。forecast.statusはconfirmed/provisional/imputed。order_basis=included（注文を予測に含む明示）が必要。元の小数予測は勝手に丸めず整数契約違反として停止する。正式発行に未確定予測は使用できない。

confirmedはyesのみ。欠損・未確認を0補完しない。商品期限はbest_before/use_byと正整数shelf_life_days。両方空欄なら期限未整備として補充計算のみ可能、14日計画を停止する。登録JANマスタ画面では全項目の明示確認を求める。

## PDF在庫の準備

1. JAN・倉庫別PLマスタを確認登録する（CSVからのマスタ取込でも計算可能）。
2. 補充出荷の「報告PDFから在庫CSVひな形を作成」を開く。
3. 神戸/杉戸それぞれの報告PDF、在庫基準日時、始業/終業、転記確認者を指定し新規保存先を選ぶ。
4. inventory.csvとsource_evidence.csvを生成。数量とconfirmedは空欄のため、そのままでは取り込めない。
5. 原本PDFと照合しJANごとのquantity_pを転記。実際に0の場合も0を明記し、確認できた行のみconfirmed=yesとする。商品が載っていないだけで0にしない。
6. products/pallets/forecast/requirementsと同じフォルダーに配置し取込。登録JANマスタを使う場合は対応チェックを選択するとproducts/palletsを置換する。旧v2登録マスタは併用しない。

PDFは自動抽出しない。20MB以下のPDF原本のバイト列をSHA-256とbase64で証跡CSVへ格納し、DB保存原本に保持する。既存の証跡CSVがなくても、inventoryのsource_pdfに指定された同フォルダーのPDF原本を読み保存できる。両方ある場合は一致を確認。source_sha256・checker・基準日時・数量・確認区分は必須。証跡はファイル同一性を確認するものであり、転記値と原本内容の業務上の一致は担当者が確認する。

読取前後にCSV/PDFの一覧・属性・内容を照合し、変更や照合不能時は停止する。同一バッチ・原子的取得は保証しない。PDFのファイル名だけで倉庫を判断せず、明示的に選択する。原本や既存CSVは上書きしない。

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

実際のムロオPDF様式/基準時刻、外部倉庫コード、めかぶの期限とPL積載数、予測の端数/注文包含/確認区分、正式補充設定。添付Excel・予測CSVの自動変換や実PDFの自動抽出は未実装。実資料の未確認値をアプリへ自動登録していない。
