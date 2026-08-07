"""今週末の重賞予想を生成して、保存・通知する。

GitHub Actions から無人で実行される想定。異常時は必ず終了コードを
0 以外にして、Actions の実行履歴が赤くなるようにしている
（黙ってダミーの予想をメールしてしまうのが一番まずいため）。
"""

import argparse
import logging
import os
import sys
from datetime import date

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


def build_pick(entry, mark):
    horse = entry['horse']
    return {
        'mark': mark,
        'umaban': horse.get('umaban', ''),
        'name': horse.get('name', ''),
        'jockey': horse.get('jockey', ''),
        'odds': horse.get('odds'),
        'ninki': horse.get('ninki'),
        'score': entry['total_score'],
        'score_rank': entry.get('score_rank'),
        'value_gap': entry.get('value_gap'),
        'is_value': entry.get('is_value', False),
        'reasons': entry['reasons'],
    }


def predict_race(scraper, analyzer, race):
    """1レースぶんの予想を組み立てる。出馬表が未発表なら None。"""
    logger.info('[%s %s] 出馬表を取得中...', race.get('grade'), race['name'])
    horses = scraper.get_horses_for_race(race)
    if not horses:
        return None

    logger.info('[%s] %d頭を評価します', race['name'], len(horses))
    evaluated = []
    for horse in horses:
        if not horse['horse_id']:
            logger.warning('%s は horse_id が取れないため除外します', horse['name'])
            continue
        details = scraper.get_horse_details(horse['horse_id'])
        evaluated.append(analyzer.evaluate_horse(horse, details))

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
        'url': race['url'],
        'field_size': analysis['field_size'],
        'picks': picks,
        'longshot': longshot,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='今週末の重賞を予想する')
    parser.add_argument('--today', help='基準日 (YYYY-MM-DD)。省略時は実行日。')
    parser.add_argument('--no-email', action='store_true', help='メールを送らない')
    parser.add_argument('--no-save', action='store_true', help='予想を保存しない')
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else None

    scraper = NetkeibaScraper()
    analyzer = RaceAnalyzer()

    logger.info('=== 週末重賞予想を開始します (メソッド %s) ===', analyzer.method_version)

    try:
        graded_races, fetched_dates = scraper.get_weekend_graded_races(today)
    except ScrapeError as exc:
        # ここで落ちるのはサイト側の変更かネットワーク障害。握りつぶさない。
        logger.error('レース一覧を取得できませんでした: %s', exc)
        return 1

    dates_label = ', '.join(d.isoformat() for d in fetched_dates)
    if not graded_races:
        logger.info('%s は対象となる重賞レースがありませんでした（正常終了）', dates_label)
        ResultsMailer().notify_no_races(dates_label, send=not args.no_email)
        return 0

    logger.info(
        '%s の重賞 %d件: %s',
        dates_label, len(graded_races), [r['name'] for r in graded_races],
    )

    predictions = []
    failures = []
    for race in graded_races:
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

        predictions.append(prediction)
        logger.info('[%s] 予想確定: %s', race['name'],
                    ' '.join(f"{p['mark']}{p['name']}" for p in prediction['picks']))

    if not predictions:
        logger.error('重賞は見つかりましたが、予想を1件も生成できませんでした: %s', failures)
        return 1

    if not args.no_save:
        path = store.save_predictions(predictions, analyzer.method_version)
        logger.info('予想を保存しました: %s', path)

    body = format_predictions(predictions, failures, analyzer.method_version)
    print('\n' + body)
    _write_job_summary(body)

    if args.no_email:
        return 0

    mailer = ResultsMailer()
    if not mailer.is_configured():
        # 認証情報が無いのは設定漏れなので、成功扱いにはしない。
        logger.error('メールの認証情報が未設定です。GitHub Secrets を確認してください。')
        return 2

    if not mailer.send(subject='【自動配信】今週末の中央競馬 重賞予想', body=body):
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
