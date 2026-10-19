"""
Переименование первой авто-стадии прогресса цикла (0 уроков этого цикла) из
«Урок 1» в «Не было урока» — закрывает П-7 из docs/renewals-tech-spec.md:
клампленный в «Урок 1» прогресс путал «только начал цикл» с «предоплаченный
следующий цикл, предыдущий ещё не отработан» (см. test_prepaid_cycle2_deal_
stays_on_no_lesson_yet). «Урок 2/3/4» сдвигаются на «Урок 1/2/3» — «Урок 4»
не сохраняется: into=4 перехватывается раньше правилом «Ждём продление»,
эта стадия физически никогда не занимает сделку.

engine.py адресует прогресс-стадии позиционно (sort_order), не по key/label —
код движка эта миграция не трогает.
"""
from django.db import migrations

# (старый key, новый key, новый label) — порядок важен, см. forward()/backward().
RENAMES = [
    ('lesson_1', 'no_lesson_yet', 'Не было урока'),
    ('lesson_2', 'lesson_1', 'Урок 1'),
    ('lesson_3', 'lesson_2', 'Урок 2'),
    ('lesson_4', 'lesson_3', 'Урок 3'),
]

OLD_LABELS = {'lesson_1': 'Урок 1', 'lesson_2': 'Урок 2',
              'lesson_3': 'Урок 3', 'lesson_4': 'Урок 4'}


def forward(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    pipe = RenewalPipeline.objects.filter(is_default=True).first()
    if pipe is None:
        return
    # По возрастанию старого индекса: каждый шаг освобождает key, который
    # использует следующий шаг (UNIQUE(pipeline, key) иначе конфликтует).
    for old_key, new_key, new_label in RENAMES:
        st = RenewalStage.objects.filter(pipeline=pipe, key=old_key).first()
        if st is not None:
            st.key = new_key
            st.label = new_label
            st.save(update_fields=['key', 'label'])


def backward(apps, schema_editor):
    RenewalPipeline = apps.get_model('renewals', 'RenewalPipeline')
    RenewalStage = apps.get_model('renewals', 'RenewalStage')
    pipe = RenewalPipeline.objects.filter(is_default=True).first()
    if pipe is None:
        return
    # В обратном порядке (по убыванию нового индекса) — та же логика освобождения key.
    for old_key, new_key, _ in reversed(RENAMES):
        st = RenewalStage.objects.filter(pipeline=pipe, key=new_key).first()
        if st is not None:
            st.key = old_key
            st.label = OLD_LABELS[old_key]
            st.save(update_fields=['key', 'label'])


class Migration(migrations.Migration):
    dependencies = [('renewals', '0008_remove_renewaldeal_insert_insert_and_more')]
    operations = [migrations.RunPython(forward, backward)]
