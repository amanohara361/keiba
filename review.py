"""保存済みの予想とレース結果を突き合わせ、的中率と回収率を記録する。

これが「予想メソッドの定期的な更新」を支える部分。毎週レースが終わった後に
実行し、data/results/ と data/accuracy.md を更新してリポジトリにコミットする。
"""

import argparse
import logging
import os
import sys

from mailer import ResultsMailer, format_review
from scraper import NetkeibaScraper
import store

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

BET_UNIT = 100  # 1点あたりの賭け金（円）


def _payout_for(payouts, bet_type, umaban):
    """指定した式別・馬番の払戻金を返す。外れていれば 0。"""
    for entry in payouts.get(bet_type, []):
        if entry['combination'].strip() == str(umaban).strip():
            return entry['yen']
    return 0


def review_race(race, result):
    """1レースぶんの答え合わせ。race は予想 JSON の1要素（破壊的に更新する）。"""
    if result is None:
        race['settled'] = False
        return race

    rank_by_umaban = {
        row['umaban']: row['rank'] for row in result['finishing_order']
    }
    payouts = result['payouts']

    staked = 0
    returned = 0

    for pick in race['picks']:
        pick['actual_rank'] = rank_by_umaban.get(pick['umaban'])

        # ◎ にだけ単勝、上位4頭すべてに複勝を買う想定
        if pick['mark'] == '◎':
            staked += BET_UNIT
            returned += _payout_for(payouts, '単勝', pick['umaban'])
        staked += BET_UNIT
        returned += _payout_for(payouts, '複勝', pick['umaban'])

    honmei = next((p for p in race['picks'] if p['mark'] == '◎'), None)
    race['honmei_won'] = bool(honmei and honmei.get('actual_rank') == 1)
    race['honmei_placed'] = bool(
        honmei and honmei.get('actual_rank') and honmei['actual_rank'] <= 3
    )

    longshot = race.get('longshot')
    if longshot:
        longshot['actual_rank'] = rank_by_umaban.get(longshot['umaban'])
        longshot_return = _payout_for(payouts, '単勝', longshot['umaban'])
        staked += BET_UNIT
        returned += longshot_return
        race['longshot_bet'] = True
        race['longshot_hit'] = longshot_return > 0
    else:
        race['longshot_bet'] = False
        race['longshot_hit'] = False

    race['settled'] = True
    race['staked'] = staked
    race['returned'] = returned
    race['payouts'] = payouts
    return race


def main(argv=None):
    parser = argparse.ArgumentParser(description='過去の予想を答え合わせする')
    parser.add_argument('--no-email', action='store_true', help='メールを送らない')
    parser.add_argument(
        '--recheck', action='store_true',
        help='答え合わせ済みのぶんもやり直す',
    )
    args = parser.parse_args(argv)

    scraper = NetkeibaScraper()
    prediction_files = store.list_prediction_files()
    if not prediction_files:
        logger.info('答え合わせ対象の予想がまだありません')
        return 0

    reviewed_any = False
    latest_races = []
    totals = {'staked': 0, 'returned': 0}

    for path in prediction_files:
        existing = store.load_results(path)
        if existing and not args.recheck:
            # すでに全レース確定済みなら再取得しない
            if all(r.get('settled') for r in existing.get('races', [])):
                continue

        payload = store.load_predictions(path)
        races = payload['races']
        logger.info('%s の %d レースを答え合わせします', path, len(races))

        for race in races:
            result = scraper.get_race_result(race['race_id'])
            review_race(race, result)
            if race['settled']:
                logger.info(
                    '[%s] 投資 %d円 / 回収 %d円', race['name'], race['staked'], race['returned']
                )
            else:
                logger.info('[%s] まだ結果が出ていません', race['name'])

        store.save_results(path, races, payload.get('method_version', 'unknown'))
        reviewed_any = True
        latest_races = races
        totals = {
            'staked': sum(r.get('staked', 0) for r in races),
            'returned': sum(r.get('returned', 0) for r in races),
        }

    if not reviewed_any:
        logger.info('新たに答え合わせすべき予想はありませんでした')
        return 0

    report_path, by_version = store.write_accuracy_report()
    logger.info('成績表を更新しました: %s', report_path)
    for version, stats in by_version.items():
        logger.info('メソッド %s: %d レース / 回収 %d円', version, stats['races'], stats['returned'])

    body = format_review(latest_races, totals)
    print('\n' + body)
    _write_job_summary(body)

    if not args.no_email:
        mailer = ResultsMailer()
        if mailer.is_configured():
            mailer.send('【自動配信】先週の重賞予想 答え合わせ', body)
        else:
            logger.warning('メール未設定のため送信をスキップしました')

    return 0


def _write_job_summary(body):
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return
    with open(summary_path, 'a', encoding='utf-8') as f:
        f.write('```\n' + body + '\n```\n')


if __name__ == '__main__':
    sys.exit(main())
