# -*- coding: utf-8 -*-
"""テスト全体の共通設定。"""

import pytest

import form


@pytest.fixture(autouse=True)
def _no_live_entries(monkeypatch):
    """直前検算は毎回出馬表（馬体重）を引く（2026-10-08〜）。テストから
    netkeiba に繋がないよう、既定では「馬体重未発表」の空を返す。"""
    monkeypatch.setattr(form, 'fetch_entries', lambda race_id, opener=None: {})
