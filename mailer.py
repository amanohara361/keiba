#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""メール送信だけを担当する。整形は呼び出し側が行う。

外部ライブラリは使わない（smtplib は標準ライブラリ）。
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate

logger = logging.getLogger(__name__)


class Mailer:
    def __init__(self):
        self.server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.port = int(os.environ.get('SMTP_PORT', '587'))
        self.sender = os.environ.get('SENDER_EMAIL')
        self.password = os.environ.get('SENDER_PASSWORD')
        self.receiver = os.environ.get('RECEIVER_EMAIL')

    def is_configured(self):
        return all([self.sender, self.password, self.receiver])

    def send(self, subject, body):
        if not self.is_configured():
            logger.error('メールの認証情報が揃っていません')
            return False

        message = MIMEText(body, 'plain', 'utf-8')
        message['From'] = self.sender
        message['To'] = self.receiver
        message['Subject'] = subject
        message['Date'] = formatdate(localtime=True)

        try:
            # 465 は SMTPS、587 は STARTTLS。どちらの設定でも動くようにする。
            if self.port == 465:
                connection = smtplib.SMTP_SSL(self.server, self.port, timeout=30)
            else:
                connection = smtplib.SMTP(self.server, self.port, timeout=30)
                connection.starttls()

            with connection:
                connection.login(self.sender, self.password)
                connection.send_message(message)

            logger.info('メールを送信しました -> %s', self.receiver)
            return True
        except Exception as exc:
            logger.error('メール送信に失敗しました: %s', exc)
            return False
