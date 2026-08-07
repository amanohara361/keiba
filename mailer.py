"""予想と答え合わせの結果をテキストに整形し、メールで送る。"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate

logger = logging.getLogger(__name__)

DIVIDER = '-' * 44


class ResultsMailer:
    def __init__(self):
        self.smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        self.sender_email = os.environ.get('SENDER_EMAIL')
        self.sender_password = os.environ.get('SENDER_PASSWORD')
        self.receiver_email = os.environ.get('RECEIVER_EMAIL')

    def is_configured(self):
        return all([self.sender_email, self.sender_password, self.receiver_email])

    def send(self, subject, body):
        if not self.is_configured():
            logger.error('メールの認証情報が揃っていません')
            return False

        message = MIMEText(body, 'plain', 'utf-8')
        message['From'] = self.sender_email
        message['To'] = self.receiver_email
        message['Subject'] = subject
        message['Date'] = formatdate(localtime=True)

        try:
            # 465 は SMTPS、587 は STARTTLS。どちらの設定でも動くようにする。
            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
                server.starttls()

            with server:
                server.login(self.sender_email, self.sender_password)
                server.send_message(message)

            logger.info('メールを送信しました -> %s', self.receiver_email)
            return True
        except Exception as exc:
            logger.error('メール送信に失敗しました: %s', exc)
            return False

    def notify_no_races(self, dates_label, send=True):
        body = (
            f'{dates_label} に該当する重賞レースはありませんでした。\n'
            'プログラムは正常に動作しています。\n'
        )
        logger.info(body.strip())
        if send and self.is_configured():
            self.send('【自動配信】今週末の重賞はありません', body)


def _odds_label(pick):
    odds, ninki = pick.get('odds'), pick.get('ninki')
    if odds and ninki:
        return f'{odds:.1f}倍/{ninki}人気'
    if odds:
        return f'{odds:.1f}倍'
    return 'オッズ未発表'


def _format_pick(pick):
    lines = [
        f"{pick['mark']} {pick['umaban']}番 {pick['name']}"
        f"（{pick['jockey']}） {_odds_label(pick)} スコア {pick['score']}"
    ]
    if pick.get('is_value'):
        lines.append(f"   ★妙味あり: 評価{pick['score_rank']}位に対して{pick['ninki']}人気")
    lines.append('   ' + ' / '.join(pick['reasons']))
    return lines


def format_predictions(predictions, failures, method_version):
    lines = [
        '🏇 今週末の中央競馬 重賞予想 🏇',
        f'（予想メソッド: {method_version}）',
        DIVIDER,
    ]

    for race in predictions:
        grade = f"[{race['grade']}] " if race.get('grade') else ''
        lines.append(f"\n【{grade}{race['name']}】{race.get('date', '')} {race['field_size']}頭立て")
        for pick in race['picks']:
            lines.extend(_format_pick(pick))

        longshot = race.get('longshot')
        if longshot:
            lines.append('\n  ▼ 高配当ねらいの穴候補')
            lines.extend('  ' + line for line in _format_pick(longshot))

        lines.append(race['url'])
        lines.append(DIVIDER)

    if failures:
        lines.append('\n※ 以下は予想を出せませんでした:')
        lines.extend(f'  - {name}' for name in failures)

    lines.append(
        '\n※ 血統・騎手・戦績・人気に基づく自動算出です。'
        '\n※ 馬券の購入は自己責任でお願いします。'
    )
    return '\n'.join(lines)


def format_review(reviewed_races, totals):
    lines = ['🏁 先週の予想 答え合わせ 🏁', DIVIDER]

    for race in reviewed_races:
        if not race.get('settled'):
            lines.append(f"\n【{race['name']}】結果がまだ確定していません")
            continue

        result_marks = []
        for pick in race['picks']:
            rank = pick.get('actual_rank')
            result_marks.append(
                f"{pick['mark']}{pick['name']}: {rank}着" if rank else
                f"{pick['mark']}{pick['name']}: 着外/除外"
            )

        lines.append(f"\n【{race['name']}】")
        lines.extend('  ' + m for m in result_marks)
        lines.append(
            f"  投資 {race['staked']}円 / 回収 {race['returned']}円"
            f"（収支 {race['returned'] - race['staked']:+,}円）"
        )

    if totals['staked']:
        roi = totals['returned'] / totals['staked'] * 100
        lines += [
            DIVIDER,
            f"今回の合計: 投資 {totals['staked']:,}円 / 回収 {totals['returned']:,}円"
            f"（回収率 {roi:.1f}%）",
        ]

    lines.append('\n通算成績は data/accuracy.md を参照してください。')
    return '\n'.join(lines)
