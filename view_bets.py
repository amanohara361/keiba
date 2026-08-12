#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""過去の買い目・検算結果を、docs/index.html と同じ見た目で見返すツール。

data/bets/ と data/checks/ はJSONのままGit履歴に残す方針（decision log参照）で、
過去分をHTML化してリポジトリに置き続けることはしない。見たいときだけこれを
実行して、その場でファイルを作る。

  python view_bets.py --date 2026-08-12            docs/history/2026-08-12.html に書く
  python view_bets.py --date 2026-08-12 --out x.html  出力先を指定する

書き出したファイルは Git 管理下に置かない（.gitignore 参照）。
その日の検算が複数回（1日に最大4回）走っていた場合、**最後の検算**
（＝実オッズが最も締まった時点の判定）を表示する。
"""

import argparse
import sys
from datetime import date

import report_html


def main(argv=None):
    parser = argparse.ArgumentParser(description='過去の買い目・検算結果をHTMLで見返す')
    parser.add_argument('--date', required=True, help='対象日 (YYYY-MM-DD)')
    parser.add_argument('--out', help='出力先。省略時は docs/history/YYYY-MM-DD.html')
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.date)
    out = args.out or f'docs/history/{day.isoformat()}.html'

    html_text = report_html.render_history(day)
    path = report_html.save(html_text, out)
    print(f'書き出しました: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
