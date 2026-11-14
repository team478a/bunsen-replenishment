# 新方式の取込前診断と項目対応

標準画面の診断は仮CSV契約v2を使用する。ブンセン在庫・朝照合・出荷ロット/期限/車両は対象外。実CSV接続や業務承認の完了を示すものではない。

入力フォルダー、対象出荷日、商品・倉庫・日付・項目の欠損/重複/異常、単位マスタ、基準日時、入庫の網羅確認・重複、必要予測期間、予測区分を検査する。不備は一覧へ表示し、欠損や不正値を推測・0で補完しない。未確定予測は参考計算を選択すべき対象として示すが、正式確定を許可しない。参考選択は日常操作の分析時に行う。

診断は業務DB・入力CSV・現在の業務データを変更せず、結果をJSON/印刷HTMLへ新規保存する。CSV読取中の変更観測(read_consistency)とatomic_snapshot=falseを記録する。後続計算が入力不備で未実施の場合は未実施と表示する。診断は正式発行可の判定ではなく、formal_issue_allowedは常にfalse。

CSV項目対応表はv2全項目を列挙し、実側の列・単位・変換・確認者は未確認で生成する。自動変換設定には使用しない。入力フォルダーへの出力・既存ファイルの上書きを拒否する。診断内容が実データを含む場合は結果も実データとして扱い、コミットしない。

```powershell
python -m bunsen.tools diagnose C:\input\csv --dispatch 2026-11-14 --output C:\review\diagnostic.json
python -m bunsen.tools mapping-template --output C:\review\mapping.json
```

配布版はBunsenTools.exe。終了コードは0=対象範囲の不備なし、2=不備あり、1=保存等の失敗（引数不正はargparseの2）。新方式CSVが必要。過去の診断を数量/入力変更後の確認として使い回さない。原子的取得・同一業務バッチ・入力の真正性は保証しない。

旧方式の診断APIは保存原本/互換性確認用に残す。現行契約は[data-contract.md](data-contract.md)、変更検査は[csv-read-guard.md](csv-read-guard.md)、旧資料はlegacy-csv-diagnostics-v0.mdを参照。
