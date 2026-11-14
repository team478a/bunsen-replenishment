# ブンセン補充出荷指示アプリ

ムロオ神戸・杉戸の確認済み在庫、既存の日別需要予測、補充条件、確定入庫から必要補充数量を計算するWindows用の独立アプリです。**ブンセン側在庫の確認・照合は不要です。特定業務Excelには依存しません。** 需要予測エンジンやAI判断は追加しません。

必要補充指示は実出荷実績・供給可能在庫と別の情報です。実CSV接続・業務設定の正式値・現場受入は未確認。samples以下の数量・設定・確認済み宣言は架空例です。

## 起動

Windows x64試用ZIP全体を展開し、BunsenReplenishment.exeを起動します。Python・Excel本体・Docker・常時ネット接続は不要です。署名なしの試用版です。

ソース版はPython 3.12（Tcl/Tk込み）で実行します。

```powershell
python -m pip install -r requirements-runtime.txt
python -m bunsen
```

## 日常操作

1. **取り込み**：新方式CSV一式（replenishment.csvを含む）を選択。
2. **分析**：出荷予定日を指定して補充計算。
3. **結果**：神戸・杉戸の現在庫、対象需要、安全在庫、必要P、換算後P、指示P、PL＋甲、到着日と日別根拠を確認。
4. **判定・確定**：修正する場合は数量・理由・担当者を記録。数量と予測区分を確認して正式確定。未確定予測は参考計算だけに使用し正式確定不可。
5. **出力**：保存済み指示から神戸・杉戸別にExcel、PDF、印刷用HTMLを新規生成。既存帳票は上書きしません。

管理設定は別タブです。サイクル・配送日数・安全日数・対象日数の変更根拠と担当者を保存し、変更後は全明細を再分析・再確認します。値を本番の正式値として自動採用しません。

## 架空データの試用

```powershell
python -m bunsen.tools acceptance-kit --dispatch 2026-11-14 --output C:\BunsenTrial\new-kit
python -m bunsen --trial-kit C:\BunsenTrial\new-kit
```

配布版はBunsenTools.exe acceptance-kitとBunsenReplenishment.exe --trial-kitを使用します。日付は実際の試験日へ置き換えてください。キット内samples/replenishmentを取込し、神戸120P・杉戸48Pの架空例を確認できます。samples/replenishment-provisionalは未確定予測を含みます。キットは試用DBを通常履歴から隔離し、帳票へ実出荷不可を表示します。

## 計算と単位

到着日=出荷予定日＋lead_days。対象日数=max(cycle_days,coverage_days)。各日の安全在庫は翌日からsafety_days日分の予測合計です。確認済み在庫を起点に日別需要・確定入庫を反映し、到着日以降に必要な安全在庫との差の最大値を補充必要Pとします。遅い入庫で早い不足を隠しません。

商品別の甲P=p_per_ball×balls_per_caseで切り上げ、PL＋甲へ表示します。1PL未満や1PLを超える端数を、無条件に丸ごとのPLへ切り上げません。元の必要P・換算後P・手修正P・最終確定Pを区別して保存します。

在庫basis=opening（基準日始業）/closing（基準日終業）を明示し、既に在庫へ反映された需要・入庫を重ねません。日中の途中在庫に日次予測全量を重ねる推測は行いません。実CSVとの時刻対応はデータ責任者が確認します。

## 履歴と互換性

通常履歴は%LOCALAPPDATA%/BunsenReplenishmentに保存します。旧指示・取込原本・改訂・バックアップは削除せず、旧方式の帳票として再出力できます。旧入力を新方式へ自動変換せず、新しい日常操作には使用しません。朝照合・ロット割当・供給在庫/期限/車両検証は新方式の判定対象外で、実出荷時の別業務です。

旧形式の互換性試験はLegacyApp/legacy_demo等で維持します。標準起動と固定日付デモは新方式です。

## 開発・検証

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python -m bunsen.demo
```

固定日付デモは2026-11-09の一時試用DBで、在庫なし計算→修正→正式確定→倉庫別Excel/PDFと未確定予測の停止を実行し、demo-output内の新規フォルダーへ出力します。

Excel出力は標準OOXMLを直接生成し、特定テンプレートや外部サービスに依存しません。日本語PDFは同梱するReportLabを使用します。[公式フォント仕様](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/)に沿った日本語CIDフォントを利用します。ライブラリは固定版を配布ZIPへ同梱し、利用端末でpipを必要としません。

詳細：[現行仕様](docs/specification.md)、[仮CSV契約](docs/data-contract.md)、[改修調査・計画](docs/replenishment-v2-plan.md)、[未確定事項](docs/open-questions.md)、[Windows配布](docs/windows-distribution.md)、[バックアップ](docs/backup-restore.md)、[CSV読取中変更検査](docs/csv-read-guard.md)。
