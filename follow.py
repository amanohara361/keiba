#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""発走の少し前に、対象レースごとの検算を起こす「目覚まし」。

## なぜこれがあるか（2026-11-11 ユーザー承認）

検算は毎時 :07 の固定時刻に走るので、最後の検算は中央で発走の27分前、地方で81分前
（中央値、2026-09-16〜11-08 の183レース）だった。同じ期間、勝ち馬に付けた確率の
対数尤度は「検算時の単勝オッズ」より「確定オッズ」が中央138レースで11.5良く、
主観を混ぜて得られる改善（1.9）の約6倍あった。買う判断と記録を確定オッズに
近づけるのが、勝率の精度にいちばん効く。

## 仕組み

1. `check.yml` / `nar_check.yml` は検算のあと `python follow.py kick` を呼ぶ。
   このあと WINDOW 分のうちに「発走 LEAD 分前」を迎える対象レースがあれば、
   `check_follow.yml` を workflow_dispatch で起こす。無ければ何もしない。
2. `check_follow.yml` は `python follow.py run` を回す。各レースの発走 LEAD 分前まで
   眠り、起きたら元の検算ワークフローを workflow_dispatch で起こす（`from_follow=true`）。
3. `from_follow=true` の検算は「問題なし」のメールを送らず（買い目が変わった・
   止めたときは送る）、目覚ましをもう一度起こさない。

眠るジョブを検算と分けているのは、検算の同時実行グループ（keiba-data）の中で
眠ると、その間ほかの収集・検算が全部待たされるため。目覚ましはデータに触らない。

標準ライブラリだけで動く。判定は一切しない（時刻を数えて起こすだけ）。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
BETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bets')

LEAD_MINUTES = 12      # 発走の何分前に検算を起こすか。起動〜オッズ取得に1〜2分かかる
WINDOW_MINUTES = 110   # 1回の目覚ましが受け持つ長さ。次の定時の検算（最長2時間半後）までをつなぐ
MERGE_MINUTES = 4      # これ以内に並ぶ起床時刻は1回にまとめる
MIN_WAIT_MINUTES = 5   # いまからこれ以内に来る起床時刻は入れない（いま走った検算で足りる）

CHECK_WORKFLOW = {'jra': 'check.yml', 'nar': 'nar_check.yml'}
FOLLOW_WORKFLOW = 'check_follow.yml'


def load_races(day):
    """その日の買い目ファイルから (org, 発走時刻) の一覧を返す。無ければ空。"""
    path = os.path.join(BETS_DIR, f'{day.isoformat()}.json')
    try:
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for race in payload.get('races') or []:
        post = _post_time(day, race.get('start_time'))
        if post is not None:
            out.append((race.get('org') or 'jra', post))
    return out


def _post_time(day, text):
    try:
        hh, mm = (int(x) for x in str(text).split(':')[:2])
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=JST)
    except (TypeError, ValueError):
        return None


def plan(races, now, org, lead=LEAD_MINUTES, window=WINDOW_MINUTES,
         merge=MERGE_MINUTES, min_wait=MIN_WAIT_MINUTES):
    """これから検算を起こす時刻の一覧（昇順）。

    対象は org のレースで、「発走 lead 分前」が now+min_wait 以降・now+window 以内のもの。
    lead 分前を過ぎた、またはすぐ迎えるレースは入れない（いま走った検算がその役を果たしている）。
    近い時刻（merge 分以内）は早いほうにまとめる。
    """
    lead_delta = timedelta(minutes=lead)
    limit = now + timedelta(minutes=window)
    start = now + timedelta(minutes=min_wait)
    times = sorted({post - lead_delta for o, post in races
                    if o == org and start <= post - lead_delta <= limit})
    out = []
    for t in times:
        if out and t - out[-1] <= timedelta(minutes=merge):
            continue
        out.append(t)
    return out


def dispatch(workflow, inputs, repo=None, ref=None, token=None, opener=None):
    """workflow_dispatch を1回送る。成功なら True。"""
    repo = repo or os.environ.get('GITHUB_REPOSITORY', '')
    ref = ref or os.environ.get('GITHUB_REF_NAME') or 'main'
    token = token or os.environ.get('GITHUB_TOKEN', '')
    if not repo or not token:
        print('GITHUB_REPOSITORY / GITHUB_TOKEN が無いので起こせません', file=sys.stderr)
        return False
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches',
        data=json.dumps({'ref': ref, 'inputs': inputs}).encode('utf-8'),
        method='POST',
        headers={'Authorization': f'Bearer {token}',
                 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28',
                 'Content-Type': 'application/json',
                 'User-Agent': 'keiba-follow'})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # 起こせなくても検算本体は止めない
        print(f'{workflow} を起こせませんでした: {exc}', file=sys.stderr)
        return False


def kick(day, now, org, dispatcher=dispatch):
    """受け持つレースがあれば目覚まし（check_follow.yml）を起こす。起こしたら True。"""
    times = plan(load_races(day), now, org)
    if not times:
        print('このあと発走前の検算を足すレースはありません')
        return False
    print('目覚ましを起こします: ' + ', '.join(t.strftime('%H:%M') for t in times))
    return dispatcher(FOLLOW_WORKFLOW, {'org': org, 'date': day.isoformat()})


def run(day, now, org, dispatcher=dispatch, sleep=time.sleep, clock=None):
    """各時刻まで眠り、検算を起こす。起こした回数を返す。"""
    clock = clock or (lambda: datetime.now(JST))
    fired = 0
    for t in plan(load_races(day), now, org):
        wait = (t - clock()).total_seconds()
        if wait > 0:
            print(f'{t.strftime("%H:%M")} まで待ちます（{wait / 60:.0f}分）', flush=True)
            sleep(wait)
        if dispatcher(CHECK_WORKFLOW[org], {'date': day.isoformat(), 'from_follow': 'true'}):
            fired += 1
            print(f'{t.strftime("%H:%M")} の検算を起こしました', flush=True)
    return fired


def main(argv=None):
    parser = argparse.ArgumentParser(description='発走前の検算を起こす目覚まし')
    parser.add_argument('command', choices=('plan', 'kick', 'run'))
    parser.add_argument('--org', choices=sorted(CHECK_WORKFLOW), default='jra')
    parser.add_argument('--date', help='対象日 (YYYY-MM-DD)。既定は今日。')
    parser.add_argument('--now', help='現在時刻 (YYYY-MM-DDTHH:MM)。動作確認用。')
    args = parser.parse_args(argv)

    now = (datetime.fromisoformat(args.now).replace(tzinfo=JST) if args.now
           else datetime.now(JST))
    day = date.fromisoformat(args.date) if args.date else now.date()

    if args.command == 'plan':
        for t in plan(load_races(day), now, args.org):
            print(t.strftime('%H:%M'))
    elif args.command == 'kick':
        kick(day, now, args.org)
    else:
        run(day, now, args.org)
    return 0


if __name__ == '__main__':
    sys.exit(main())
