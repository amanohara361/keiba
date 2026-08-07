"""今週末の重賞予想を生成して、保存・通知する。

GitHub Actions から無人で実行される想定。異常時は必ず終了コードを
0 以外にして、Actions の実行履歴が赤くなるようにしている
（黙ってダミーの予想をメールしてしまうのが一番まずいため）。

配信は1日3回:
  morning … 朝。馬体重も馬場状態もまだ出ていない段階の叩き台
  update  … 馬体重と馬場状態が出そろった頃の更新
  final   … 発走前。最終オッズと直前の馬場変化を反映
update と final は「その日の、まだ発走していないレース」だけを対象にし、
前回配信からの変化（馬場が渋った、オッズが動いた、◎が替わった）を併記する。
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta

from analyzer import RaceAnalyzer
from mailer import ResultsMailer, format_predictions
from scraper import NetkeibaScraper, ScrapeError
import store

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

MARKS = ['◎', '○', '▲', '△']

# 発走までこの分数を切ったレースは配信しない（届いても間に合わないため）
RACE_CUTOFF_MINUTES = 10

# オッズがこの割合以上動いたら「変化」として報告する
ODDS_MOVE_RATIO = 0.3

MODES = {
    'morning': {
        'label': '朝の予想',
        'subject': '【自動配信】今週末の中央競馬 重賞予想',
        'today_only': False,
    },
    'update': {
        'label': '馬体重・馬場発表後の更新',
        'subject': '【自動配信・更新】重賞予想（馬体重/馬場を反映）',
        'today_only': True,
    },
    'final': {
        'label': '直前の最終予想',
        'subject': '【自動配信・直前】重賞予想 最終',
        'today_only': True,
    },
}


def build_pick(entry, mark):
    horse = entry['horse']
    return {
        'mark': mark,
        'umaban': horse.get('umaban', ''),
        'name': horse.get('name', ''),
        'jockey': horse.get('jockey', ''),
        'odds': horse.get('odds'),
        'ninki': horse.get('ninki'),
        'weight': horse.get('weight'),
        'weight_diff': horse.get('weight_diff'),
        'score': entry['total_score'],
        'score_rank': entry.get('score_rank'),
        'value_gap': entry.get('value_gap'),
        'is_value': entry.get('is_value', False),
        'reasons': entry['reasons'],
    }


def is_upcoming(race, now):
    """まだ発走していないレースかどうか。発走時刻が不明なら含める側に倒す。"""
    if not race.get('start_time'):
        return True
    try:
        race_day = date.fromisoformat(race['date'])
        hour, minute = (int(part) for part in race['start_time'].split(':'))
    except (ValueError, KeyError):
        return True

    start = datetime.combine(race_day, datetime.min.time()).replace(
        hour=hour, minute=minute, tzinfo=now.tzinfo,
    )
    return start - timedelta(minutes=RACE_CUTOFF_MINUTES) > now


def select_races(races, mode, now):
    """モードに応じて配信対象のレースを絞る。"""
    upcoming = [r for r in races if is_upcoming(r, now)]
    if MODES[mode]['today_only']:
        today = now.date().isoformat()
        upcoming = [r for r in upcoming if r.get('date') == today]
    return upcoming


def predict_race(scraper, analyzer, race):
    """1レースぶんの予想を組み立てる。出馬表が未発表なら None。"""
    logger.info('[%s %s] 出馬表を取得中...', race.get('grade'), race['name'])
    card = scraper.get_race_card(race)
    horses, conditions = card['horses'], card['conditions']
    if not horses:
        return None

    if conditions.get('going'):
        logger.info('[%s] 馬場: %s / 天候: %s', race['name'],
                    conditions['going'], conditions.get('weather') or '不明')

    logger.info('[%s] %d頭を評価します', race['name'], len(horses))
    evaluated = []
    for horse in horses:
        if horse.get('scratched'):
            continue
        if not horse['horse_id']:
            logger.warning('%s は horse_id が取れないため除外します', horse['name'])
            continue
        details = scraper.get_horse_details(horse['horse_id'])
        evaluated.append(analyzer.evaluate_horse(horse, details, conditions))

    if not evaluated:
        return None

    analysis = analyzer.analyze_race(evaluated, top_n=len(MARKS))
    picks = [build_pick(e, MARKS[i]) for i, e in enumerate(analysis['top_picks'])]
    longshot = build_pick(analysis['longshot'], '穴') if analysis['longshot'] else None

    return {
        'race_id': race['race_id'],
        'name': race['name'],
        'grade': race.get('grade'),
        'date': race.get('date'),
        'start_time': race.get('start_time'),
        'url': race['url'],
        'field_size': analysis['field_size'],
        'conditions': conditions,
        'picks': picks,
        'longshot': longshot,
    }


def detect_changes(previous, current):
    """前回配信からの変化を日本語で列挙する。無ければ空リスト。"""
    if not previous:
        return []

    changes = []

    prev_going = (previous.get('conditions') or {}).get('going')
    going = (current.get('conditions') or {}).get('going')
    if going and going != prev_going:
        changes.append(
            f'馬場が {prev_going or "未発表"} → {going} に変わりました'
            if prev_going else f'馬場状態が発表されました: {going}'
        )

    prev_honmei = next((p for p in previous['picks'] if p['mark'] == '◎'), None)
    honmei = next((p for p in current['picks'] if p['mark'] == '◎'), None)
    if prev_honmei and honmei and prev_honmei['name'] != honmei['name']:
        changes.append(f"◎が {prev_honmei['name']} → {honmei['name']} に替わりました")

    prev_odds = {p['name']: p.get('odds') for p in previous['picks']}
    for pick in current['picks']:
        before, after = prev_odds.get(pick['name']), pick.get('odds')
        if not before or not after:
            continue
        if abs(after - before) / before >= ODDS_MOVE_RATIO:
            direction = '下落' if after < before else '上昇'
            changes.append(
                f"{pick['name']} のオッズが {before:.1f} → {after:.1f} 倍に{direction}"
            )

    prev_values = {p['name'] for p in previous['picks'] if p.get('is_value')}
    for pick in current['picks']:
        if pick.get('is_value') and pick['name'] not in prev_values:
            changes.append(f"{pick['name']} が新たに妙味ありになりました")

    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description='今週末の重賞を予想する')
    parser.add_argument('--mode', choices=sorted(MODES), default='morning',
                        help='配信の種類（既定: morning）')
    parser.add_argument(
        '--today',
        help='基準日時。YYYY-MM-DD なら時刻は現在のまま、'
             'YYYY-MM-DDTHH:MM なら時刻も固定する（発走前の絞り込みの確認用）。',
    )
    parser.add_argument('--no-email', action='store_true', help='メールを送らない')
    parser.add_argument('--no-save', action='store_true', help='予想を保存しない')
    args = parser.parse_args(argv)

    now = store.now_jst()
    if args.today:
        # 手動テスト用。日付だけ渡されたら時刻は現在時刻を使う。
        if 'T' in args.today:
            now = datetime.fromisoformat(args.today).replace(tzinfo=store.JST)
        else:
            override = date.fromisoformat(args.today)
            now = now.replace(year=override.year, month=override.month, day=override.day)

    mode = MODES[args.mode]
    scraper = NetkeibaScraper()
    analyzer = RaceAnalyzer()

    logger.info('=== %s を開始します (メソッド %s) ===', mode['label'], analyzer.method_version)

    try:
        graded_races, fetched_dates = scraper.get_weekend_graded_races(now.date())
    except ScrapeError as exc:
        # ここで落ちるのはサイト側の変更かネットワーク障害。握りつぶさない。
        logger.error('レース一覧を取得できませんでした: %s', exc)
        return 1

    weekend_key = fetched_dates[0].isoformat()
    dates_label = ', '.join(d.isoformat() for d in fetched_dates)
    targets = select_races(graded_races, args.mode, now)

    if not graded_races:
        logger.info('%s は対象となる重賞レースがありませんでした（正常終了）', dates_label)
        if args.mode == 'morning':
            ResultsMailer().notify_no_races(dates_label, send=not args.no_email)
        return 0

    if not targets:
        # 当日ぶんが終わっていれば追加配信は不要。異常ではない。
        logger.info('配信対象のレースがありません（すべて発走済みか対象日外）')
        return 0

    logger.info('対象の重賞 %d件: %s', len(targets), [r['name'] for r in targets])

    previous = store.load_weekend_predictions(weekend_key)
    previous_by_id = {r['race_id']: r for r in (previous or {}).get('races', [])}

    predictions = []
    failures = []
    for race in targets:
        try:
            prediction = predict_race(scraper, analyzer, race)
        except ScrapeError as exc:
            logger.error('[%s] の予想に失敗しました: %s', race['name'], exc)
            failures.append(race['name'])
            continue

        if prediction is None:
            logger.warning('[%s] は出馬表が未発表のためスキップします', race['name'])
            failures.append(f"{race['name']}（出馬表未発表）")
            continue

        prediction['changes'] = detect_changes(previous_by_id.get(race['race_id']), prediction)
        predictions.append(prediction)
        logger.info('[%s] 予想確定: %s', race['name'],
                    ' '.join(f"{p['mark']}{p['name']}" for p in prediction['picks']))
        for change in prediction['changes']:
            logger.info('[%s] 変化: %s', race['name'], change)

    if not predictions:
        logger.error('重賞は見つかりましたが、予想を1件も生成できませんでした: %s', failures)
        return 1

    if not args.no_save:
        # 週末ごとに1ファイルへ集約し、レース単位で上書きする。
        # こうすると朝の予想が直前の予想で更新され、答え合わせは最終版に対して行われる。
        path = store.save_predictions(predictions, analyzer.method_version, weekend_key)
        logger.info('予想を保存しました: %s', path)

    body = format_predictions(predictions, failures, analyzer.method_version, mode['label'])
    print('\n' + body)
    _write_job_summary(body)

    if args.no_email:
        return 0

    mailer = ResultsMailer()
    if not mailer.is_configured():
        # 認証情報が無いのは設定漏れなので、成功扱いにはしない。
        logger.error('メールの認証情報が未設定です。GitHub Secrets を確認してください。')
        return 2

    if not mailer.send(subject=mode['subject'], body=body):
        return 1

    logger.info('=== 正常に完了しました ===')
    return 0


def _write_job_summary(body):
    """Actions の実行結果画面にも予想を出す。メールが死んでも結果は残る。"""
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return
    with open(summary_path, 'a', encoding='utf-8') as f:
        f.write('```\n' + body + '\n```\n')


if __name__ == '__main__':
    sys.exit(main())
