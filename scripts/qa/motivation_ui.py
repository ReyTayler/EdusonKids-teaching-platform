"""UI мотивации Eduson через локальный nginx. Все API-данные синтетические.
Запуск: python scripts/qa/motivation_ui.py. Нужны Playwright и Edge.
"""
import copy
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts' / 'motivation-ui'
TODAY = dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date()
URL = '/api/admin/payroll/rate-versions'
DIRECTIONS = [dict(id=1, name='Python', color=None), dict(id=2, name='Scratch', color=None)]


def version(pk, day, group='900', individual='600', editable=False, current=True):
    return dict(id=pk, effective_from=day.isoformat(), group_rate=group, individual_rate=individual,
                cancel_mode='percent', cancel_value='50', penalty_enabled=True,
                penalty_mode='per_lesson', penalty_amount='40', is_current=current, editable=editable,
                created_by=1, created_by_name='Тестовый администратор', created_at='2026-11-23T08:00:00Z',
                directions=[dict(direction_id=1, direction_name='Python', group_rate='1100', individual_rate=None),
                            dict(direction_id=2, direction_name='Scratch', group_rate=None, individual_rate=None)])


class Fixture:
    def __init__(self, empty=False, role='superadmin'):
        self.versions = [] if empty else [version(1, TODAY - dt.timedelta(days=10))]
        self.role, self.writes, self.fail_save, self.fail_load = role, [], False, False

    def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/auth/me':
            route.fulfill(json=dict(account_id=1, email='qa@example.invalid', role=self.role,
                                    teacher_id=None, name='Тестовый сотрудник', twofa_enabled=True)); return
        if path == '/api/auth/csrf':
            route.fulfill(json={}, headers={'Set-Cookie': 'csrftoken=qa; Path=/'}); return
        if path.startswith(URL):
            if request.method == 'GET':
                if self.fail_load:
                    route.fulfill(status=503, json={'error': 'Тестовая ошибка загрузки'}); return
                route.fulfill(json={'results': self.versions, 'directions': DIRECTIONS,
                                    'no_rate': {'lessons': 2 if not self.versions else 0, 'earliest': '2026-11-01' if not self.versions else None}}); return
            body = copy.deepcopy(request.post_data_json)
            self.writes.append((request.method, path, body))
            assert set(body) == {'effective_from', 'group_rate', 'individual_rate', 'cancel_mode', 'cancel_value',
                                 'penalty_enabled', 'penalty_mode', 'penalty_amount', 'directions'}, body
            if self.fail_save:
                route.fulfill(status=400, json={'error': 'Ошибка валидации', 'details': {
                    'effective_from': ['Версия с этой даты уже существует.']}}); return
            if request.method == 'PATCH':
                item = next(item for item in self.versions if item['id'] == int(path.rsplit('/', 1)[1]))
            else:
                for item in self.versions: item['is_current'] = False
                item = version(10, TODAY, editable=False)
                self.versions.insert(0, item)
            item.update(body)
            route.fulfill(status=200 if request.method == 'PATCH' else 201, json={**item, 'recalculated': 2}); return
        if path == '/api/admin/payroll/personal-rates':
            route.fulfill(json={'rows': [], 'total': 0, 'page': 1, 'page_size': 50}); return
        if path == '/api/admin/teachers': route.fulfill(json=[]); return
        if path == '/api/admin/groups':
            route.fulfill(json={'rows': [], 'total': 0, 'page': 1, 'page_size': 50}); return
        if 'notifications' in path or 'tasks' in path:
            route.fulfill(json={'count': 0, 'results': [], 'unread': 0}); return
        if path.endswith('/settings'):
            route.fulfill(json={}); return
        route.fulfill(json=[])


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        for mobile in [False, True]:
            fixture = Fixture()
            ctx = browser.new_context(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 1000},
                                      timezone_id='Europe/Moscow', is_mobile=mobile, has_touch=mobile)
            ctx.route('**/api/**', fixture.route)
            page = ctx.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:8180/admin/settings')
            page.locator('#splash').wait_for(state='detached')
            section = page.get_by_role('region', name=re.compile(r'^Групповые занятия'))
            expect(section).to_be_visible()
            expect(section.get_by_text('1 100 ₽', exact=True).first).to_be_visible()
            assert not fixture.writes
            page.screenshot(path=str(OUT / ('mobile-overview.png' if mobile else 'desktop-overview.png')), full_page=True, animations='disabled')
            # Форматы — представления одной общей версии, без новых API и схем.
            page.get_by_role('tab', name='Индивидуальные занятия', exact=True).click()
            section = page.get_by_role('region', name=re.compile(r'^Индивидуальные занятия'))
            expect(section).to_be_visible()
            assert 'motivation=individual' in page.url
            expect(section.locator('.motivation-rule__amount').first).to_contain_text('600 ₽')
            page.get_by_role('tab', name='Персональные ставки', exact=True).click()
            expect(page.get_by_role('heading', name='Персональные ставки', exact=False)).to_be_visible()
            page.get_by_role('tab', name='Групповые занятия', exact=True).click()
            section = page.get_by_role('region', name=re.compile(r'^Групповые занятия'))
            section.get_by_role('button', name='Изменить с даты…').click()
            dialog = page.get_by_role('dialog', name='Ставки школы с даты', exact=True)
            expect(dialog).to_be_visible()
            group = dialog.get_by_role('spinbutton', name='Групповой урок, по умолчанию', exact=True)
            group.fill('950.25')
            expect(dialog.get_by_label('Проверка новых условий').get_by_text('950,25 ₽', exact=True).first).to_be_visible()
            dialog.get_by_text('Ставки по направлениям', exact=False).first.click()
            direction = dialog.get_by_role('spinbutton', name='Python: групповой урок', exact=True)
            direction.fill('1200')
            dialog.get_by_text('Штрафовать за отчёт позже дня занятия', exact=True).click()
            expect(dialog.get_by_role('checkbox', name='Штрафовать за отчёт позже дня занятия', exact=True)).not_to_be_checked()
            # Переключение режима отмены — штатный SelectInput внутри модалки.
            dialog.get_by_role('button', name='Как считать оплату за отмену').click()
            page.get_by_role('option', name='Фикс, ₽', exact=True).click()
            dialog.get_by_role('spinbutton', name='Сумма за отмену, ₽', exact=True).fill('150')
            dialog.locator('.modal-body').evaluate('(el) => { el.scrollTop = 0; }')
            page.screenshot(path=str(OUT / ('mobile-form.png' if mobile else 'desktop-form.png')), animations='disabled')
            fixture.fail_save = True
            dialog.get_by_role('button', name='Сохранить', exact=True).click()
            expect(dialog.get_by_text('Версия с этой даты уже существует.', exact=True)).to_be_visible()
            expect(group).to_have_value('950.25')
            fixture.fail_save = False
            dialog.get_by_role('button', name='Сохранить', exact=True).click()
            expect(dialog).to_have_count(0)
            body = fixture.writes[-1][2]
            assert body['group_rate'] == '950.25' and body['individual_rate'] == '600'
            assert body['directions'][0]['group_rate'] == '1200' and body['directions'][0]['individual_rate'] is None
            assert body['cancel_mode'] == 'fixed' and body['cancel_value'] == '150' and body['penalty_enabled'] is False
            assert len(fixture.writes) == 2
            # Историческая версия доступна для копирования, но не для исправления.
            section.get_by_text('История и будущие изменения', exact=False).first.click()
            old = section.locator('.motivation-history__entry').last
            old.locator('summary').first.click()
            expect(old.get_by_role('button', name='Исправить версию')).to_have_count(0)
            expect(old.get_by_role('button', name='Удалить версию')).to_have_count(0)
            old.get_by_role('button', name='Создать на основе этой версии').click()
            expect(dialog).to_be_visible()
            expect(group).to_have_value('900')
            page.keyboard.press('Escape')
            expect(dialog).to_have_count(0)
            # Будущая версия редактируется PATCH, не создаёт независимую ставку формата.
            fixture.versions.insert(0, version(2, TODAY + dt.timedelta(days=7), group='1500', editable=True, current=False))
            page.reload()
            section.get_by_role('button', name='Открыть версию').click()
            edit = page.get_by_role('dialog', name='Исправить ставки школы', exact=True)
            expect(edit).to_be_visible()
            edit.get_by_role('spinbutton', name='Групповой урок, по умолчанию').fill('1600')
            edit.get_by_role('button', name='Сохранить', exact=True).click()
            expect(edit).to_have_count(0)
            assert fixture.writes[-1][0:2] == ('PATCH', URL + '/2')
            page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            section.get_by_role('button', name='Изменить с даты…').click()
            expect(dialog).to_be_visible()
            page.screenshot(path=str(OUT / ('mobile-dark.png' if mobile else 'desktop-dark.png')), animations='disabled')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.keyboard.press('Escape')
            assert not errors, errors
            ctx.close()
        # До первой версии нет ставок; ошибка загрузки не маскируется нулевыми суммами.
        fixture = Fixture(empty=True)
        ctx = browser.new_context()
        ctx.route('**/api/**', fixture.route)
        page = ctx.new_page()
        page.goto('http://127.0.0.1:8180/admin/settings')
        expect(page.get_by_text('Ставки не заданы', exact=True)).to_be_visible()
        expect(page.get_by_text('Уроков без ставки:', exact=False)).to_be_visible()
        fixture.fail_load = True
        page.reload()
        expect(page.get_by_text('Не удалось загрузить ставки школы', exact=True)).to_be_visible(timeout=20000)
        expect(page.get_by_text('Ставки не заданы', exact=True)).to_have_count(0)
        fixture.fail_load = False
        page.get_by_role('button', name='Повторить', exact=True).click()
        expect(page.get_by_text('Ставки не заданы', exact=True)).to_be_visible()
        assert not fixture.writes
        ctx.close()
        fixture = Fixture(role='manager')
        ctx = browser.new_context()
        ctx.route('**/api/**', fixture.route)
        page = ctx.new_page()
        page.goto('http://127.0.0.1:8180/admin/settings')
        expect(page.get_by_role('tab', name='Групповые занятия', exact=True)).to_have_count(0)
        ctx.close()
        browser.close()
    print(json.dumps({'result': 'PASS', 'screenshots': str(OUT)}, ensure_ascii=False))


if __name__ == '__main__':
    run()
