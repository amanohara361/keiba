import json

import bet_type_map


def _race(rid, pops, payouts):
    return {'race_id': rid, '日付': '20261101',
            'horses': [{'馬番': n, '人気': str(p)} for n, p in pops.items()],
            '払戻': payouts}


# 4頭：1番=1番人気、2番=2番人気、3番=5番人気、4番=8番人気。着順 3→1→4
RACE = _race('r1', {1: 1, 2: 2, 3: 5, 4: 8}, {
    '単勝': [{'combination': [3], 'yen': 900}],
    '複勝': [{'combination': [3], 'yen': 300}, {'combination': [1], 'yen': 110},
             {'combination': [4], 'yen': 700}],
    'ワイド': [{'combination': [1, 3], 'yen': 500}, {'combination': [3, 4], 'yen': 2000},
               {'combination': [1, 4], 'yen': 900}],
    '馬連': [{'combination': [1, 3], 'yen': 1200}],
    '馬単': [{'combination': [3, 1], 'yen': 2500}],
    '3連複': [{'combination': [1, 3, 4], 'yen': 8000}],
    '3連単': [{'combination': [3, 1, 4], 'yen': 40000}],
})


def _write(tmp_path, races):
    (tmp_path / '20261101_東京.json').write_text(json.dumps(races, ensure_ascii=False), encoding='utf-8')


def test_skips_races_without_payouts(tmp_path):
    no_pay = dict(RACE, race_id='r2')
    del no_pay['払戻']
    _write(tmp_path, [RACE, no_pay])
    assert [r['race_id'] for r in bet_type_map.load_races(str(tmp_path))] == ['r1']


def test_tally_counts_bets_and_hits(tmp_path):
    _write(tmp_path, [RACE])
    t = bet_type_map.tally(bet_type_map.load_races(str(tmp_path)))
    assert t['単勝']['4〜5番人気'] == {'bets': 1, 'hits': 1, 'returned': 900, 'max': 900}
    assert t['単勝']['1番人気']['hits'] == 0
    # ワイド：A-A は 1-2 の1点、A-B は 1-3・2-3 の2点で 1-3 が的中
    assert t['ワイド']['A-A']['bets'] == 1
    assert t['ワイド']['A-B'] == {'bets': 2, 'hits': 1, 'returned': 500, 'max': 500}
    assert t['ワイド']['B-C']['returned'] == 2000
    # 馬単は着順どおり：3(B)→1(A) は B-A
    assert t['馬単']['B-A']['hits'] == 1 and t['馬単']['A-B']['hits'] == 0
    assert t['馬単']['A-B']['bets'] == 2
    # 3連単は並びを問わない帯で、組の数は並び順込み（A-B-C の3頭は6通り×2組）
    assert t['3連単']['A-B-C']['bets'] == 12
    assert t['3連単']['A-B-C']['returned'] == 40000
    assert t['3連複']['A-B-C']['bets'] == 2


def test_render_lines(tmp_path):
    _write(tmp_path, [RACE])
    text = '\n'.join(bet_type_map.report(str(tmp_path)))
    assert 'data_jra の 1レース' in text
    assert '| 単勝 | 4〜5番人気 | 1 | 1 | 900% | 0% |' in text


def test_empty_when_no_payouts(tmp_path):
    _write(tmp_path, [])
    assert bet_type_map.report(str(tmp_path)) == []
