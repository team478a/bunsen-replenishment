"""Actionable UI messages; structured validation issues remain unchanged in history."""
from .core import DataError
INPUT_CODES={'INPUT_CHANGED','INPUT_RECHECK_FAILED'}

def describe_error(error):
    issues=error.issues if isinstance(error,DataError) else []
    targets=[i for i in issues if i.code in INPUT_CODES]
    if not targets:return '不備・未確認 — 処理を停止しました',str(error)
    lines=['入力ファイルを安全に読み込めなかったため、処理を止めました。',
           '読み込み中の変更、移動、またはファイルを開けなかった可能性があります。',
           '', '対象ファイルと理由：']
    for issue in targets:lines.extend(['・'+issue.filename,'  '+issue.message])
    lines.extend(['','対処方法：','1. 対象ファイルを編集している場合は、保存して閉じてください。',
                  '2. ファイルの移動・コピー・同期が終わってから、もう一度「データを開いて計算」を押してください。',
                  '3. 繰り返す場合は、入力データ一式を別の作業フォルダーへコピーして、そちらを選んでください。'])
    others=[text for text in error.errors if text not in {str(i) for i in targets}]
    if others:lines.extend(['','あわせて確認する内容：']+others)
    return '入力ファイルを読み込めませんでした','\n'.join(lines)
