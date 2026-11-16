# ムロオ必要補充方式の架空試用キット

BunsenTools.exe acceptance-kit --dispatch YYYY-MM-DD --output 新規フォルダーで、v2の架空CSV・GUIDE.md・未実施受入記録を生成します。BunsenReplenishment.exe --trial-kit キットで起動し、通常履歴と分離したtrial-dataを使用します。

4シナリオ20CSVを含みます。samples/replenishmentは確認済み宣言の架空例、samples/replenishment-provisionalは未確定予測の例、samples/delivery-planは概算期限付きの14日計画例、samples/report-many-productsは8商品の長い品名・商品別PL換算・多ページ帳票・16PL分便の例です。ブンセン在庫・朝照合は不要。設定値・基準時刻・確認済み宣言は実データや業務承認を意味しません。サンプルは試験日07時のムロオ在庫宣言なので、それ以前は未来データとして停止します。これは朝の供給在庫確認条件ではありません。

元サンプル・生成CSVのハッシュを記録し、変更/欠損を検出したキットは起動しません。新版・別日には新しいキットを生成します。旧キットは履歴資料として保持し、新方式へ自動変換しません。

GUIDE.mdと[受入手順](acceptance-test.md)で人が実結果・日時・担当者・証拠先を記入します。初期状態は全項目未実施、承認未確認。自動テストで現場結果を成功へ変更しません。試用帳票は実出荷に使用しないでください。

14項目の受入記録はexpectedに期待結果を記載し、actual/evidence/operator/performed_atを人が記入します。商品マスタ画面の登録・適用・不足停止も確認します。同梱OPERATIONAL-READINESS.md（[原本](operational-readiness.md)）で実CSV・正式マスタ・計算照合・実プリンター・保存/復旧/配布の確認を残します。多品目例の初回は商品ごと神戸432P/杉戸216P、倉庫合計3456P/1728P、神戸19枠→2便（16+3）です。

旧版キットはアプリ版が異なるため新規生成してください。旧キット・履歴は削除しません。長文のExcel印刷は行高を調整し、上限を超える場合はセル位置を示して停止します。PDFは長い行をページ内で分割します。実機での最終的な表示・印刷適合は受入記録へ残してください。
