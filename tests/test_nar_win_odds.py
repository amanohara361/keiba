import io
import json
import zipfile

import nar
import review

RID = nar.race_id('大井', '20261006', 2)


def _odds_zip(rows):
    header = '競馬場,競走年月日,レース番号,賭式,番号1,番号2,番号3,オッズ,オッズ（最大）,人気\n'
    body = ''.join(','.join(r) + '\n' for r in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('20261001_odds.csv', ('﻿' + header + body).encode('utf-8'))
    return buf.getvalue()


def test_parse_win_odds_keeps_only_win():
    data = _odds_zip([
        ['大井', '20261006', '2', '単勝', '1', '', '', '3.0', '', '2'],
        ['大井', '20261006', '2', '複勝', '1', '', '', '1.2', '1.5', '1'],
        ['大井', '20261006', '2', '３連単', '1', '2', '3', '55.1', '', '9'],
        ['大井', '20261006', '2', '単勝', '2', '', '', '"1,234.5"', '', '12'],
    ])
    assert nar.parse_win_odds(data) == {
        ('大井', '20261006', 2, 1): 3.0, ('大井', '20261006', 2, 2): 1234.5}


def _result():
    return {'race_id': RID, 'payouts': {},
            'finishing_order': [{'rank': 1, 'umaban': 1, 'ninki': 2},
                                {'rank': 2, 'umaban': 2, 'ninki': 1}]}


def test_add_win_odds():
    result = _result()
    table = {('大井', '20261006', 2, 1): 3.0, ('大井', '20261006', 2, 2): 2.1}
    assert nar.add_win_odds(result, odds_fetcher=lambda month: table) == 2
    assert [h['odds'] for h in result['finishing_order']] == [3.0, 2.1]


def test_add_win_odds_failure_keeps_result():
    result = _result()
    assert nar.add_win_odds(result) == 0      # conftest で取得失敗にしている
    assert 'odds' not in result['finishing_order'][0]


def test_cached_nar_result_gets_odds_once(tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'RESULTS_DIR', str(tmp_path))
    (tmp_path / f'{RID}.json').write_text(json.dumps(_result(), ensure_ascii=False), encoding='utf-8')
    calls = []

    def fake(month, opener=None):
        calls.append(month)
        return {('大井', '20261006', 2, 1): 3.0, ('大井', '20261006', 2, 2): 2.1}
    monkeypatch.setattr(nar, 'monthly_win_odds', fake)
    first = review.get_result(RID, org='nar')
    assert first['finishing_order'][0]['odds'] == 3.0
    saved = json.loads((tmp_path / f'{RID}.json').read_text(encoding='utf-8'))
    assert saved['finishing_order'][1]['odds'] == 2.1
    review.get_result(RID, org='nar')
    assert len(calls) == 1                    # 保存し直したので2回目は取りに行かない
