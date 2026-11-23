"""Общий SQL UNION: поиск и пагинация выполняются до загрузки строк в Python."""
from django.db.models import CharField, Count, Q, Value
from django.db.models.functions import Concat, Trim

from apps.directions.models import Direction
from apps.groups.models import Group
from apps.teachers.models import Teacher


def _search(query: str, fields: tuple[str, ...]) -> Q:
    result = Q()
    for word in query.split():
        term = Q()
        for field in fields:
            term |= Q(**{f'{field}__icontains': word})
        # #123 и 123 ищут ID во всех типах; большие числа не уходят в int4 PostgreSQL.
        number = word.removeprefix('#')
        if number.isascii() and number.isdecimal() and len(number) <= 10:
            value = int(number)
            if 0 < value <= 2_147_483_647:
                term |= Q(id=value)
        result &= term
    return result


def archive_query(query: str, entity_type: str):
    sources = [
        ('teacher', Teacher, ('name', 'email', 'phone'),
         Trim(Concat('email', Value(' '), 'phone', output_field=CharField()))),
        ('group', Group, ('name', 'direction__name', 'teacher__name'),
         Concat('direction__name', Value(' · '), 'teacher__name', output_field=CharField())),
        ('direction', Direction, ('name',), Value('', output_field=CharField())),
    ]
    counts, archive_counts, queries = {}, {}, []
    for kind, model, fields, description in sources:
        base = model.objects.filter(active=False).order_by()
        predicate = _search(query, fields)
        totals = base.aggregate(total=Count('pk'), matched=Count('pk', filter=predicate))
        counts[kind], archive_counts[kind] = totals['matched'], totals['total']
        if entity_type in ('all', kind):
            queries.append(base.filter(predicate).annotate(
                entity_type=Value(kind, output_field=CharField()), description=description,
            ).values('id', 'name', 'entity_type', 'description'))
    rows = queries[0].union(*queries[1:], all=True) if len(queries) > 1 else queries[0]
    return rows.order_by('name', 'entity_type', 'id'), counts, archive_counts
