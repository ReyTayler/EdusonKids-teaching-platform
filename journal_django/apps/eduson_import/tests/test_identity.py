"""Склейка участников Eduson в учеников (спека §5.2): один ребёнок — только когда
совпала вся связка признаков; спорные пары не склеиваются и уходят на ручную проверку."""
from __future__ import annotations

from apps.eduson_import.identity import resolve_students


def p(pid, child, parent, phone, age, **kw):
    """Строка participants в том виде, в каком её отдаёт EdusonSource.rows()."""
    row = {
        'id': pid, 'group_id': kw.pop('g', pid), 'child_name': child, 'child_tg': '', 'child_phone': '',
        'child_email': '', 'parent_name': parent, 'parent_phone': phone, 'parent_tg': '', 'parent_email': '',
        'age': age, 'amo_url': '', 'notes': '',
    }
    row.update(kw)
    return row


def groups_of(res):
    return [s.participant_ids for s in res.students]


def reviews_of(res):
    return [(r.participant_ids, r.reason) for r in res.manual_review]


def test_same_child_two_groups_merged():
    a = p(1, 'Миша', 'Анна', '8 999 000-00-00', 12, g=10)
    b = p(2, ' миша ', 'АННА', '+7 (999) 000 00 00', 12, g=11)
    res = resolve_students([a, b])
    assert groups_of(res) == [[1, 2]]
    assert res.students[0].rows == [a, b]
    assert res.manual_review == []


def test_namesakes_with_different_parents_not_merged():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 12), p(2, 'Миша', 'Ольга', '79991111111', 9)])
    assert groups_of(res) == [[1], [2]]
    assert res.manual_review == []  # телефоны разные — спорной пары нет


def test_siblings_share_phone_not_merged():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 12), p(2, 'Катя', 'Анна', '79990000000', 9)])
    assert groups_of(res) == [[1], [2]]
    assert res.manual_review == []


def test_shared_crm_link_email_tg_alone_do_not_merge():
    common = {'amo_url': 'https://crm.example/leads/1', 'parent_email': 'family@example.ru', 'parent_tg': '@family'}
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 13, **common),
                            p(2, 'Катя', 'Анна', '79992222222', 9, **common)])
    assert groups_of(res) == [[1], [2]]


def test_same_name_and_contacts_but_other_phone_not_merged():
    # почта и CRM одинаковые, имя то же — но телефон родителя другой: не склеиваем
    common = {'amo_url': 'https://crm.example/leads/1', 'parent_email': 'family@example.ru'}
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10, **common),
                            p(2, 'Миша', 'Анна', '79993333333', 10, **common)])
    assert groups_of(res) == [[1], [2]]


def test_age_conflict_goes_to_manual_review():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 7), p(2, 'Миша', 'Анна', '79990000000', 12)])
    assert groups_of(res) == [[1], [2]]
    assert reviews_of(res) == [([1, 2], 'возраст')]


def test_parent_name_conflict_goes_to_manual_review():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10), p(2, 'Миша', 'Ольга', '79990000000', 10)])
    assert groups_of(res) == [[1], [2]]
    assert reviews_of(res) == [([1, 2], 'имя родителя')]


def test_both_conflicts_reported_together():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 7), p(2, 'Миша', 'Ольга', '79990000000', 12)])
    assert reviews_of(res) == [([1, 2], 'возраст, имя родителя')]


def test_age_off_by_one_and_empty_parent_name_merge():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10), p(2, 'Миша', '', '79990000000', 11)])
    assert groups_of(res) == [[1, 2]]
    assert res.manual_review == []


def test_unknown_age_on_one_side_merges():
    res = resolve_students([p(1, 'Ёлка', 'Анна', '79990000000', None), p(2, 'Елка', 'Анна', '79990000000', 9)])
    assert groups_of(res) == [[1, 2]]


def test_age_off_by_two_is_conflict():
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10), p(2, 'Миша', 'Анна', '79990000000', 12)])
    assert groups_of(res) == [[1], [2]]
    assert reviews_of(res) == [([1, 2], 'возраст')]


def test_third_record_must_agree_with_every_record_of_candidate():
    """Правило для 3+ записей: новая запись сравнивается с КАЖДОЙ уже склеенной.
    1 и 2 склеены (возраст 10/11, имя родителя пусто у второй). 3 (возраст 9) с 1 совпадает
    (±1), но с 2 расходится на 2 года — значит, это не тот же ребёнок наверняка: не
    склеиваем и отдаём всю тройку на ручную проверку."""
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10),
                            p(2, 'Миша', '', '79990000000', 11),
                            p(3, 'Миша', 'Анна', '79990000000', 9)])
    assert groups_of(res) == [[1, 2], [3]]
    assert reviews_of(res) == [([1, 2, 3], 'возраст')]


def test_third_record_parent_conflicts_with_second_only():
    # 1 без имени родителя, 2 — «Анна»: склеены. 3 — «Ольга»: с 1 нет противоречия, с 2 есть.
    res = resolve_students([p(1, 'Миша', '', '79990000000', 10),
                            p(2, 'Миша', 'Анна', '79990000000', 10),
                            p(3, 'Миша', 'Ольга', '79990000000', 10)])
    assert groups_of(res) == [[1, 2], [3]]
    assert reviews_of(res) == [([1, 2, 3], 'имя родителя')]


def test_rejected_record_can_join_other_candidate_with_same_key():
    # 1 (возраст 7) и 2 (возраст 12) — разные дети с тем же именем и телефоном (на проверку);
    # 3 (возраст 12) — тот же ребёнок, что и 2: с ним склеивается, конфликт с 1 — на проверку.
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 7),
                            p(2, 'Миша', 'Анна', '79990000000', 12),
                            p(3, 'Миша', 'Анна', '79990000000', 12)])
    assert groups_of(res) == [[1], [2, 3]]
    assert reviews_of(res) == [([1, 2], 'возраст'), ([1, 3], 'возраст')]


def test_missing_name_or_phone_never_merges():
    res = resolve_students([p(1, 'Миша', 'Анна', '', 10), p(2, 'Миша', 'Анна', '', 10),
                            p(3, '', 'Анна', '79990000000', 10), p(4, '  ', 'Анна', '79990000000', 10)])
    assert groups_of(res) == [[1], [2], [3], [4]]
    assert res.manual_review == []


def test_deterministic_order_independent_of_input_order():
    rows = [p(5, 'Катя', 'Анна', '79990000000', 9), p(3, 'Миша', 'Анна', '79990000000', 10),
            p(1, 'Миша', 'Анна', '89990000000', 10), p(4, 'Миша', 'Анна', '79990000000', 15)]
    res = resolve_students(rows)
    assert groups_of(res) == [[1, 3], [4], [5]]
    assert reviews_of(res) == [([1, 3, 4], 'возраст')]
    assert groups_of(resolve_students(list(reversed(rows)))) == groups_of(res)


def test_age_stored_as_text_is_understood():
    # SQLite не держит тип колонки: возраст мог лечь строкой
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', '10'), p(2, 'Миша', 'Анна', '79990000000', 11)])
    assert groups_of(res) == [[1, 2]]


def test_ambiguous_record_not_glued_to_first_candidate():
    # 1 (7 лет) и 2 (12 лет) — разные дети; 3 без возраста и имени родителя подходит обоим
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 7),
                            p(2, 'Миша', 'Анна', '79990000000', 12),
                            p(3, 'Миша', '', '79990000000', None)])
    assert groups_of(res) == [[1], [2], [3]]
    assert reviews_of(res) == [([1, 2], 'возраст'),
                               ([1, 2, 3], 'неоднозначно: подходит нескольким ученикам')]


def test_empty_legacy_rows_skipped_not_students():
    empty = p(7, '', '', '', None, amo_url='https://crm.example/leads/9')  # ссылку CRM legacy берёт у группы
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 10), empty,
                            p(8, '  ', '', '', '')])
    assert groups_of(res) == [[1]]
    assert res.skipped_empty == [7, 8]
    assert res.manual_review == []


def test_row_with_only_age_or_contact_is_not_empty():
    res = resolve_students([p(1, '', '', '', 9), p(2, '', '', '', None, child_tg='@kid')])
    assert groups_of(res) == [[1], [2]]
    assert res.skipped_empty == []


def test_namesakes_with_same_stub_phone_not_merged():
    for stub in ('79999999999', '+7 000 000-00-00', '8 (123) 456-78-90', '00000', '7999'):
        res = resolve_students([p(1, 'Миша', 'Анна', stub, 10), p(2, 'Миша', 'Анна', stub, 10)])
        assert groups_of(res) == [[1], [2]], stub
        assert res.manual_review == [], stub


def test_ambiguous_record_is_not_a_candidate_for_later_rows():
    # C (3) подходит и A (1), и B (2) → отдельный и спорный; D (4) — тот же ребёнок, что B,
    # и не должен «спотыкаться» о C
    res = resolve_students([p(1, 'Миша', 'Анна', '79990000000', 7),
                            p(2, 'Миша', 'Анна', '79990000000', 12),
                            p(3, 'Миша', '', '79990000000', None),
                            p(4, 'Миша', 'Анна', '79990000000', 12)])
    assert groups_of(res) == [[1], [2, 4], [3]]
    assert reviews_of(res) == [([1, 2], 'возраст'),
                               ([1, 2, 3], 'неоднозначно: подходит нескольким ученикам'),
                               ([1, 4], 'возраст')]
