"""予想と結果をリポジトリ内の JSON として保存する。

クラウドで動かすうえで一番大事なのがこの層。予想を残しておかないと
「メソッドを更新して当たるようになったのか」を後から検証できない。
GitHub Actions が結果をコミットして戻すので、履歴がそのまま成績表になる。
"""

import json
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
PREDICTIONS_DIR = os.path.join(DATA_DIR, 'predictions')
RESULTS_DIR = os.path.join(DATA_DIR, 'results')
ACCURACY_PATH = os.path.join(DATA_DIR, 'accuracy.md')


def now_jst():
    return datetime.now(JST)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def _read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def weekend_path(weekend_key):
    """週末（土曜の日付）ごとの予想ファイルのパス。"""
    return os.path.join(PREDICTIONS_DIR, f'{weekend_key}.json')


def load_weekend_predictions(weekend_key):
    """その週末の保存済み予想。まだ無ければ None。"""
    path = weekend_path(weekend_key)
    return _read_json(path) if os.path.exists(path) else None


def save_predictions(races, method_version, weekend_key):
    """予想を週末ごとの1ファイルにまとめて保存する。

    1日に複数回（朝・馬体重発表後・直前）配信するため、レース単位で上書きする。
    こうすると土曜の朝に出した日曜ぶんの予想を消さずに、
    直前予想だけを差し替えられる。答え合わせは最終版に対して行われる。
    """
    existing = load_weekend_predictions(weekend_key) or {}
    merged = {r['race_id']: r for r in existing.get('races', [])}
    for race in races:
        merged[race['race_id']] = race

    payload = {
        'weekend': weekend_key,
        'generated_at': now_jst().isoformat(),
        'method_version': method_version,
        'races': sorted(merged.values(), key=lambda r: (r.get('date', ''), r['race_id'])),
    }
    return _write_json(weekend_path(weekend_key), payload)


def list_prediction_files():
    if not os.path.isdir(PREDICTIONS_DIR):
        return []
    return sorted(
        os.path.join(PREDICTIONS_DIR, name)
        for name in os.listdir(PREDICTIONS_DIR)
        if name.endswith('.json')
    )


def load_predictions(path):
    return _read_json(path)


def result_path_for(prediction_path):
    return os.path.join(RESULTS_DIR, os.path.basename(prediction_path))


def load_results(prediction_path):
    path = result_path_for(prediction_path)
    return _read_json(path) if os.path.exists(path) else None


def save_results(prediction_path, races, method_version):
    payload = {
        'reviewed_at': now_jst().isoformat(),
        'method_version': method_version,
        'races': races,
    }
    return _write_json(result_path_for(prediction_path), payload)


def all_result_files():
    if not os.path.isdir(RESULTS_DIR):
        return []
    return sorted(
        os.path.join(RESULTS_DIR, name)
        for name in os.listdir(RESULTS_DIR)
        if name.endswith('.json')
    )


def write_accuracy_report():
    """保存済みの結果すべてを集計して data/accuracy.md を書き直す。

    メソッドのバージョンごとに分けて集計する。バージョンを上げた前後で
    的中率・回収率がどう変わったかを見るのがこのファイルの目的。
    """
    by_version = {}
    total_races = 0

    for path in all_result_files():
        payload = _read_json(path)
        version = payload.get('method_version', 'unknown')
        bucket = by_version.setdefault(version, {
            'races': 0, 'win_hits': 0, 'place_hits': 0,
            'staked': 0, 'returned': 0, 'longshot_bets': 0, 'longshot_hits': 0,
        })

        for race in payload.get('races', []):
            if not race.get('settled'):
                continue
            bucket['races'] += 1
            total_races += 1
            bucket['win_hits'] += 1 if race.get('honmei_won') else 0
            bucket['place_hits'] += 1 if race.get('honmei_placed') else 0
            bucket['staked'] += race.get('staked', 0)
            bucket['returned'] += race.get('returned', 0)
            if race.get('longshot_bet'):
                bucket['longshot_bets'] += 1
                bucket['longshot_hits'] += 1 if race.get('longshot_hit') else 0

    lines = [
        '# 予想メソッドの成績',
        '',
        f'最終更新: {now_jst().strftime("%Y-%m-%d %H:%M")} JST / 集計対象 {total_races} レース',
        '',
        '賭け方の前提: ◎に単勝100円、◎○▲△の4頭に複勝100円ずつ、',
        '穴候補がいればそこに単勝100円（合計 500〜600円/レース）。',
        '',
        '| メソッド | レース数 | ◎勝率 | ◎複勝率 | 投資 | 回収 | 回収率 | 穴的中 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]

    for version in sorted(by_version):
        b = by_version[version]
        races = b['races']
        if not races:
            continue
        roi = (b['returned'] / b['staked'] * 100) if b['staked'] else 0.0
        longshot = (
            f"{b['longshot_hits']}/{b['longshot_bets']}" if b['longshot_bets'] else '-'
        )
        lines.append(
            f"| {version} | {races} | {b['win_hits'] / races:.1%} | "
            f"{b['place_hits'] / races:.1%} | {b['staked']:,}円 | "
            f"{b['returned']:,}円 | {roi:.1f}% | {longshot} |"
        )

    if not by_version:
        lines.append('| （まだ結果がありません） | - | - | - | - | - | - | - |')

    lines += [
        '',
        '回収率が 100% を超えていれば理論上プラス。下回っている間は',
        'analyzer.py の WEIGHTS を調整し、METHOD_VERSION を上げて比較する。',
        '',
    ]

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ACCURACY_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return ACCURACY_PATH, by_version
