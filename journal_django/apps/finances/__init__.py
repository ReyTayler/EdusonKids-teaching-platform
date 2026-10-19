"""
apps.finances — чистый вычислительный слой финансов (FIFO + баланс).

БЕЗ моделей и urls: это не Django-app, а библиотека, которую используют
payments, payroll, dashboard. Единый дом для:
  • fifo.py        — порт services/fifo.js computeFifo (Decimal, бухгалтерская точность).
  • repository.py  — SQL для FIFO-входов (партии/посещения) и баланса по направлению.
  • balance.py     — баланс ученика (purchased − attended), числа как int/float по Express.

Не регистрируется в INSTALLED_APPS (нет моделей). Raw SQL идёт через
django.db.connection и работает без регистрации app.
"""
