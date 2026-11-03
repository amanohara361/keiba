from datetime import date

import bet_gates


class FakeRace:
    def __init__(self, marks, partners=()):
        self.marks = [{'umaban': u, 'mark': m} for u, m in marks.items()]
        self.partners = [{'umaban': u} for u in partners]

    def mark_of(self, umaban):
        return next((m['mark'] for m in self.marks if m['umaban'] == umaban), None)


def _result(order, odds=True):
    """order は着順どおりの [(馬番, 人気, 単勝オッズ)]。"""
    return {'finishing_order': [
        {'rank': i + 1, 'umaban': u, 'ninki': n, **({'odds': o} if odds else {})}
        for i, (u, n, o) in enumerate(order)]}


ORDER = [(1, 2, 4.0), (2, 1, 2.0), (3, 3, 8.0), (4, 4, 16.0)]


def test_horse_rows_groups_marks_and_partners():
    race = FakeRace({1: '◎', 2: '○'}, partners=[4])
    rows = bet_gates.horse_rows(race, _result(ORDER))
    assert [r['group'] for r in rows] == ['◎', '○', '', '穴']
    assert rows[0] == {'group': '◎', 'ninki': 2, 'rank': 1, 'odds': 4.0}


def test_horse_rows_skips_race_without_popularity():
    race = FakeRace({1: '◎'})
    result = _result(ORDER)
    result['finishing_order'][2]['ninki'] = None
    assert bet_gates.horse_rows(race, result) is None


def test_horse_rows_allows_missing_odds():
    race = FakeRace({1: '◎'})
    rows = bet_gates.horse_rows(race, _result(ORDER, odds=False))
    assert rows[0]['odds'] is None


def test_win_expectation_uses_market_odds():
    race = FakeRace({1: '◎'})
    rows = bet_gates.horse_rows(race, _result(ORDER))
    out = bet_gates.analyze([(date(2026, 11, 1), 'jra', rows)])
    win = out['jra']['total']['◎']['win']
    inv = 1 / 4 + 1 / 2 + 1 / 8 + 1 / 16
    assert win['actual'] == 1
    assert abs(win['expected'] - (1 / 4) / inv) < 1e-9


def test_win_expectation_falls_back_to_popularity_without_odds():
    # 2レース：2番人気は1回勝って1回負けた → 人気なりの勝率は0.5
    race = FakeRace({1: '◎'})
    a = bet_gates.horse_rows(race, _result(ORDER, odds=False))
    b = bet_gates.horse_rows(FakeRace({9: '◎'}), _result(
        [(2, 1, None), (1, 2, None), (3, 3, None), (4, 4, None)], odds=False))
    out = bet_gates.analyze([(date(2026, 11, 1), 'nar', a), (date(2026, 11, 2), 'nar', b)])
    win = out['nar']['total']['◎']['win']
    assert win['n'] == 1 and win['actual'] == 1
    assert abs(win['expected'] - 0.5) < 1e-9


def _many(n_races, honmei_wins):
    """◎がいつも4番人気で、最初の honmei_wins レースだけ勝つ。"""
    out = []
    for i in range(n_races):
        if i < honmei_wins:
            order = [(1, 4, 16.0), (2, 1, 2.0), (3, 2, 4.0), (4, 3, 8.0)]
        else:
            order = [(2, 1, 2.0), (3, 2, 4.0), (4, 3, 8.0), (1, 4, 16.0)]
        day = date(2026, 9, 1 + i % 28)
        out.append((day, 'jra', bet_gates.horse_rows(FakeRace({1: '◎'}), _result(order))))
    return out


def test_gate_not_judged_below_min_horses():
    out = bet_gates.analyze(_many(10, 10))
    gate = next(g for g in out['jra']['gates'] if g['name'] == '単勝')
    assert not gate['ready']
    assert '判定不可' in gate['missing'][0]


def test_gate_ready_when_far_above_market_in_both_halves():
    # 市場の勝率は約6.7%。40レース中20勝なら大きく上回る
    data = _many(40, 0)
    data = [(d, o, bet_gates.horse_rows(FakeRace({1: '◎'}), _result(
        [(1, 4, 16.0), (2, 1, 2.0), (3, 2, 4.0), (4, 3, 8.0)] if i % 2 == 0 else
        [(2, 1, 2.0), (3, 2, 4.0), (4, 3, 8.0), (1, 4, 16.0)])))
            for i, (d, o, _) in enumerate(data)]
    out = bet_gates.analyze(data)
    gate = next(g for g in out['jra']['gates'] if g['name'] == '単勝')
    assert gate['ready'] and gate['in_use']
    # 使用中の券種はメールで知らせない
    assert bet_gates.mail_line(out) is None


def test_mail_line_for_unused_gate():
    analysis = {'jra': {'gates': [{'name': 'ワイド', 'ready': True, 'in_use': False}]}}
    assert '中央のワイド' in bet_gates.mail_line(analysis)


def test_render_lines_has_tables():
    out = bet_gates.analyze(_many(40, 5))
    text = '\n'.join(bet_gates.render_lines(out))
    assert '### 中央（40レース' in text
    assert '| 単勝（使用中） |' in text
    assert '| ◎ | 勝ち |' in text
