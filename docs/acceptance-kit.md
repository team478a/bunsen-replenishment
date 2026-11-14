# ムロオ必要補充方式の架空試用キット

BunsenTools.exe acceptance-kit --dispatch YYYY-MM-DD --output 新規フォルダーで、v2の架空CSV・GUIDE.md・未実施受入記録を生成します。BunsenReplenishment.exe --trial-kit キットで起動し、通常履歴と分離したtrial-dataを使用します。

samples/replenishmentは確認済み宣言の架空例、samples/replenishment-provisionalは未確定予測の例です。ブンセン在庫・朝照合は不要。設定値・基準時刻・確認済み宣言は実データや業務承認を意味しません。サンプルは試験日07時のムロオ在庫宣言なので、それ以前は未来データとして停止します。これは朝の供給在庫確認条件ではありません。

元サンプル・生成CSVのハッシュを記録し、変更/欠損を検出したキットは起動しません。新版・別日には新しいキットを生成します。旧キットは履歴資料として保持し、新方式へ自動変換しません。

GUIDE.mdと[受入手順](acceptance-test.md)で人が実結果・日時・担当者・証拠先を記入します。初期状態は全項目未実施、承認未確認。自動テストで現場結果を成功へ変更しません。試用帳票は実出荷に使用しないでください。
