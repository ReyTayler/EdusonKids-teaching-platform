"""Структурное сравнение двух PostgreSQL-БД (по DSN).

Сравнивает множества: колонки (table,col,type,nullable,default),
CHECK/UNIQUE/PK/FK-определения (нормализованные, без имён), индексы (def без имени).
Печатает только расхождения. Пустой вывод = схемы структурно совпадают.

Usage:
  python journal_django/scripts/compare_schema.py "<dsn_a>" "<dsn_b>"
"""
import re
import sys

import psycopg2

SKIP_TABLES = {'schema_migrations', 'django_migrations'}


def _norm(s: str) -> str:
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def fetch(dsn):
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name, column_name, data_type, is_nullable,
               COALESCE(column_default,''), COALESCE(numeric_precision,0),
               COALESCE(numeric_scale,0), COALESCE(character_maximum_length,0)
        FROM information_schema.columns
        WHERE table_schema='public'
    """)
    cols = {(t, c): (dt, n, _norm(d), p, s, ml)
            for t, c, dt, n, d, p, s, ml in cur.fetchall() if t not in SKIP_TABLES}
    cur.execute("""
        SELECT conrelid::regclass::text, contype, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE connamespace='public'::regnamespace AND contype IN ('c','u','p','f')
    """)
    cons = {}
    for t, ct, d in cur.fetchall():
        if t in SKIP_TABLES:
            continue
        cons.setdefault(t, set()).add((ct, _norm(d)))
    cur.execute("""
        SELECT tablename, indexdef FROM pg_indexes WHERE schemaname='public'
    """)
    idx = {}
    for t, d in cur.fetchall():
        if t in SKIP_TABLES:
            continue
        d2 = _norm(re.sub(r'index \S+ on', 'index on', d))
        idx.setdefault(t, set()).add(d2)
    conn.close()
    return cols, cons, idx


def main():
    a, b = sys.argv[1], sys.argv[2]
    ca, cona, ia = fetch(a)
    cb, conb, ib = fetch(b)

    diffs = []
    for key in sorted(set(ca) | set(cb)):
        if ca.get(key) != cb.get(key):
            diffs.append(f'COLUMN {key}: A={ca.get(key)} B={cb.get(key)}')
    for t in sorted(set(cona) | set(conb)):
        for x in cona.get(t, set()) - conb.get(t, set()):
            diffs.append(f'CONSTRAINT only in A [{t}]: {x}')
        for x in conb.get(t, set()) - cona.get(t, set()):
            diffs.append(f'CONSTRAINT only in B [{t}]: {x}')
    for t in sorted(set(ia) | set(ib)):
        for x in ia.get(t, set()) - ib.get(t, set()):
            diffs.append(f'INDEX only in A [{t}]: {x}')
        for x in ib.get(t, set()) - ia.get(t, set()):
            diffs.append(f'INDEX only in B [{t}]: {x}')

    if diffs:
        print('\n'.join(diffs))
        sys.exit(1)
    print('OK: схемы структурно идентичны')


if __name__ == '__main__':
    main()
