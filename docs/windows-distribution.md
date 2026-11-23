# Windows試用配布・導入・更新

対象はWindows x64。現在の成果物は`0.7.6-preview.1`の署名なし試用版。実CSV接続・業務設定・本番配布の承認は未完了。Windows 10/11の現場端末での受入は別途実施する。

## 利用端末への導入

1. CIのWindows packageジョブが成功したコミットの成果物を取得する。GitHub Releaseの公開版ではない。
2. 同梱の`.zip.sha256`とZIPのSHA-256を照合し、ZIP全体を新しいフォルダーへ展開する。
3. `BunsenReplenishment.exe`を起動する。`_internal`・samples・docsも必要なのでexeだけを移動しない。
4. 画面タイトルの版と`build-info.json`のコミットを記録し、架空サンプルで受入を行う。

利用端末にPython・pip・Docker・常時ネット接続は不要。標準GUIはアプリ本体の展開先の`data`へ業務データを保存する。新規展開先は独立した空データで起動し、旧共有先へ自動移行・切替をしない。旧版を使う場合はメニューで共有先を明示的に開く。[保存先と初期化](storage-and-reset.md)を参照。管理者権限や自動起動は要求しない。署名警告・端末の保護機能による停止は、管理担当者が配布元・ハッシュ・方針を確認する。保護機能の無効化は導入手順に含めない。

## 更新・前版への切替

1. 必要な画面編集を仮指示として保存し、アプリのバックアップ機能でZIPを保存・検証する。
2. すべての旧画面を閉じる。新旧を同時運用しない。
3. 新版を別フォルダーへ展開し、旧フォルダーへ上書きしない。業務データを配布フォルダーへコピーしない。
4. 変更内容のDB互換性を確認し、履歴・版・架空診断・現場受入を確認して利用する。

この版はCSV列・DBテーブルを変更しない。前版への切替もアプリ本体だけで行い、業務DBを古いバックアップへ巻き戻さない。将来のDB変更を伴う版には個別の移行手順が必要。アンインストール時に業務データを自動削除する機能はない。

## コンソール版

```powershell
.\BunsenTools.exe --version
.\BunsenTools.exe self-test --output C:\test\runtime-result.json
.\BunsenTools.exe demo --output C:\test\demo
.\BunsenTools.exe backup create C:\data\BunsenReplenishment D:\backup\new.zip
.\BunsenTools.exe backup inspect D:\backup\new.zip
.\BunsenTools.exe backup restore D:\backup\new.zip C:\recovery
.\BunsenTools.exe diagnose C:\input\csv --dispatch 2026-11-10 --output C:\review\diagnostic.json
.\BunsenTools.exe mapping-template --output C:\review\csv-mapping-review.json
.\BunsenReplenishment.exe --data-dir C:\recovery\restored-...
```

診断は架空サンプル・一時DBを使用し、業務DBへ接続しない。復元履歴は従来どおり閲覧専用。PDFはブラウザー経由の印刷で、実印刷の受入は別途必要。

## 開発者向けビルド

Windows x64 / Python 3.12（Tcl/Tk込み）とGitを用意し、ネット接続のあるビルド環境で実行する。

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe scripts\build_windows.py
python scripts\check_distribution.py dist\BunsenReplenishment-...zip --result dist\package-check.json
```

PyInstaller 6.22.3とビルド依存を固定する。利用端末にはこれらをインストールしない。各ビルドに実際のPython版・コミット・未コミット変更の有無・署名なし状態・作成日時を記録する。バイナリがビット単位で再現することは保証しない。

配布検証はZIPを別の日本語・空白入りパスに展開し、PythonをPATHから外して両exeを起動する。ファイル一覧・ハッシュ、Tcl/Tk起動、取込・計算・仮指示再開・複数ロット正式発行・バックアップ／閲覧専用復元・デモ帳票を確認する。これはPython未導入のクリーンな現場PCでの実機試験の代替ではない。

## 同梱物と出典

コードのほかはGit管理された文書・架空サンプルと、明示したライセンスだけを収集する。data、output、履歴DB、認証情報は収集しない。`package-manifest.json`は自分自身を除くファイル一覧・SHA-256を記録し、ZIPのハッシュは別ファイルに記録する。ハッシュは破損検出用で、発行元の電子署名ではない。

Python配布物のLICENSE.txt、インストールされたTkのlicense.terms、[Tcl 8.6.15のlicense.terms](https://github.com/tcltk/tcl/blob/core-8-6-15/license.terms)、ビルドツールのライセンスを同梱する。ビルド方式は[PyInstallerのspec仕様](https://pyinstaller.org/en/stable/spec-files.html)と[実行時パスの仕様](https://pyinstaller.org/en/stable/runtime-information.html)を参照。

署名証明書・発行元、本番配布先、インストーラー、自動更新、端末管理との連携は未対応。現場承認後の段階で整備する。

## 隔離した現場受入キット

`BunsenTools.exe acceptance-kit --dispatch YYYY-MM-DD --output 新規フォルダー`で架空CSVと未実施記録を作成し、`BunsenReplenishment.exe --trial-kit キットフォルダー`で起動します。版が異なるキットは新規生成してください。通常履歴を使用せず実時計の検証を維持し、試用帳票に実出荷不可と表示します。手順は[受入キット](acceptance-kit.md)を参照してください。

## 補充前の14日参考見通し

GUIの専用タブまたは`BunsenTools.exe outlook CSVフォルダー --start YYYY-MM-DD --output 新規JSON`から利用できます。予測欠損の影響日以降は確認不能とし、仮/正式指示を反映しません。詳細は[14日参考見通し](reference-outlook.md)を参照してください。

## CSV読取中の変更検査

通常取込・診断・参考CLIは共有の変更検出を通します。配布自己診断でstable_observed/atomic_snapshot=falseを確認します。取込中の変更・再照合不能時は停止し、更新完了後の再取込が必要です。同一バッチや常時監視を保証しません。[検査仕様](csv-read-guard.md)を参照してください。

## v2への更新

標準画面・デモ・新規試用キットはムロオ必要補充方式です。既存履歴は同じDBで保持し、旧指示は従来HTMLで閲覧/再出力します。旧CSVや旧キットは新方式に自動変換せず、新版のキットを新規生成してください。新方式の設定変更・指示はworkflow=2で識別します。

ReportLabと実行依存をrequirements-runtime.txtで固定し、ビルド環境とZIPへ含めます。利用端末のExcel本体・pip・ネット接続は不要です。ソース実行では先にruntime依存をインストールしてください。Excel/PDFはアプリの保存済み指示から直接生成し、特定帳票への差込はしません。
# 0.3.0-preview.1（14日配送計画）

期限マスタと14日計画を追加。配布自己診断は既存互換に加え、計算日固定の概算期限・16PL枠・担当者確認・倉庫別Excel/PDF出力を隔離DBで検証する。旧データは期限マスタ追加前でも既存補充操作を継続できる。実期限/重量/容積/休日や最適性の保証は含まない。

## 0.3.1-preview.1（帳票・受入準備）

受入キットに14日計画・8商品の長名例を含む4シナリオ20CSV、13項目の期待結果、OPERATIONAL-READINESS.mdを追加。アプリ版に合う新規キットを生成する。旧キットや履歴は削除しない。多品目正式指示・計画出力とキットの未実施状態保持を配布自己診断に追加し、両exe各14項目を検証する。Excelの長文行高は見積りのため、現場で実表示/印刷の照合が必要。

## 0.4.0-preview.1（商品マスタ画面）

商品コード/品名/換算/出荷単位/期限を独立した画面で確認保存し、再起動後も利用する。日常操作で登録マスタを明示選択した取込と、旧CSV方式を区別する。キットは商品マスタの確認を加え14項目。配布自己診断は画面登録→登録版で取込→正式指示/使用版/Excel・PDF/CSV新規出力を含む両exe各15項目。旧DBのテーブルを変更せず、旧帳票/原本とバックアップを保持する。旧版キットは更新せず新版で新規生成する。
