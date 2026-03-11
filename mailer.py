import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging

class ResultsMailer:
    """予想結果をメールに整形して送信するメーラー"""
    
    def __init__(self):
        # 環境変数から設定を取得
        self.smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.sender_email = os.environ.get('SENDER_EMAIL')
        self.sender_password = os.environ.get('SENDER_PASSWORD') 
        self.receiver_email = os.environ.get('RECEIVER_EMAIL')

    def _format_body(self, results_by_race):
        """レース毎の予想結果をテキストフォーマットにする"""
        if not results_by_race:
            return "今週末の対象レースは見つかりませんでした。\n"
            
        lines = []
        lines.append("🏇 今週末の中央競馬 重賞予想結果 🏇\n")
        lines.append("-" * 40)
        
        for race_name, picks in results_by_race.items():
            lines.append(f"\n【{race_name}】")
            if not picks:
                lines.append("出走馬データが取得できませんでした。")
                continue
                
            for i, pick in enumerate(picks, 1):
                horse_name = pick['horse'].get('name', '不明')
                jockey = pick['horse'].get('jockey', '不明')
                score = pick['total_score']
                reasons = " / ".join(pick['reasons'])
                
                mark = ["◎", "○", "▲", "△"][i-1] if i <= 4 else f"{i}番手"
                lines.append(f"{mark} {horse_name} (騎手: {jockey}) - 評価スコア: {score}")
                lines.append(f"   理由: {reasons}")
                
            lines.append("\n" + "-" * 40)
            
        lines.append("\n※この予想は血統・騎手・戦績などの独自スコアリングに基づく自動算出です。馬券の購入は自己責任でお願いします。")
        return "\n".join(lines)

    def send_email(self, results_by_race):
        """整形したインフォメーションを送信する"""
        if not all([self.sender_email, self.sender_password, self.receiver_email]):
            logging.error("Email credentials are not fully set in environment variables.")
            return False
            
        subject = "【自動配信】今週末の中央競馬 重賞予想"
        body = self._format_body(results_by_race)
        
        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['To'] = self.receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            text = msg.as_string()
            server.sendmail(self.sender_email, self.receiver_email, text)
            server.quit()
            logging.info("Email sent successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed to send email: {e}")
            return False

if __name__ == "__main__":
    # テスト用
    mailer = ResultsMailer()
    dummy_results = {
        'G1 桜花賞': [
            {
                'horse': {'name': 'テスト牝馬', 'jockey': 'ルメール'},
                'total_score': 85.5,
                'reasons': ['好血統（父: ディープインパクト） (+15)', 'トップジョッキー（ルメール） (+20)', '豊富なキャリア (+15)']
            }
        ]
    }
    print(mailer._format_body(dummy_results))
