# Windows試用配布・導入・更新

対象はWindows x64。初期成果物は`0.1.0-preview.1`の署名なし試用版。実CSV接続・業務設定・本番配布の承認は未完了。Windows 10/11の現場端末での受入は別途実施する。

## 利用端末への導入

1. CIのWindows packageジョブが成功したコミットの成果物を取得する。GitHub Releaseの公開版ではない。
2. 同梱の`.zip.sha256`とZIPのSHA-256を照合し、ZIP全体を新しいフォルダーへ展開する。
3. `BunsenReplenishment.exe`を起動する。`_internal`・samples・docsも必要なのでexeだけを移動しない。
4. 画面タイトルの版と`build-info.json`のコミットを記録し、架空サンプルで受入を行う。

利用端末にPython・pip・Docker・常時ネット接続は不要。アプリ本体の展開先とは別に、従来の`%LOCALAPPDATA%/BunsenReplenishment`へ業務データを保存する。管理者権限や自動起動は要求しない。署名警告・端末の保護機能による停止は、管理担当者が配布元・ハッシュ・方針を確認する。保護機能の無効化は導入手順に含めない。

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
