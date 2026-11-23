"""Проверка рабочей высоты списков Eduson на синтетических данных через nginx."""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
from motivation_ui import Fixture, ROOT

OUT = ROOT / 'artifacts' / 'main-content-ui'


class ListsFixture(Fixture):
    def route(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/admin/teachers':
            route.fulfill(json=[dict(id=i, name=f'Тестовый преподаватель {i}', active=True,
                                     email='', phone='') for i in range(1, 81)]); return
        if path == '/api/admin/students':
            rows = [dict(id=i, full_name=f'Тестовый ученик {i}', active=True, birth_date=None,
                         parent1_name='', parent1_phone='', stage_id=None, platform_id=None,
                         manager_name='', current_groups=[]) for i in range(1, 51)]
            route.fulfill(json={'rows': rows, 'total': 80, 'page': 1, 'page_size': 50}); return
        super().route(route)


def run(baseline=False):
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        for width, height in [(1440, 900), (1366, 768), (390, 844)]:
            fixture = ListsFixture()
            ctx = browser.new_context(viewport={'width': width, 'height': height}, timezone_id='Europe/Moscow')
            ctx.route('**/api/**', fixture.route)
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for section in ['teachers', 'students']:
                page.goto(f'http://127.0.0.1:8180/admin/{section}')
                page.locator('#splash').wait_for(state='detached')
                area = page.locator('.table-wrap').first
                expect(area.locator('tbody tr').first).to_be_visible()
                if not baseline:
                    page.wait_for_function("getComputedStyle(document.querySelector('.table-wrap')).getPropertyValue('--table-max-height').trim() !== ''")
                geometry = page.evaluate('''() => {
                  const page = document.querySelector('.app-page');
                  const area = document.querySelector('.table-wrap');
                  const panel = area.closest('.table-panel');
                  const paginator = panel.querySelector('.paginator');
                  return {padding: parseFloat(getComputedStyle(page).paddingBottom),
                    top: area.getBoundingClientRect().top, height: area.clientHeight,
                    bottom: panel.getBoundingClientRect().bottom,
                    viewport: innerHeight, overflow: area.scrollHeight > area.clientHeight,
                    paginatorBottom: paginator?.getBoundingClientRect().bottom ?? null,
                    horizontalOverflow: document.documentElement.scrollWidth > innerWidth};
                }''')
                if not baseline:
                    assert geometry['padding'] == 16, geometry
                    assert geometry['overflow'], geometry
                    # Border and fractional rounding can add a few CSS pixels.
                    assert 14 <= height - geometry['bottom'] <= 28, geometry
                    assert not geometry['horizontalOverflow'], geometry
                    if geometry['paginatorBottom']:
                        assert geometry['paginatorBottom'] <= height - 12, geometry
                results.append(dict(section=section, width=width, **geometry))
                area.evaluate('(el) => { el.scrollTop = el.scrollHeight; }')
                assert area.evaluate('(el) => el.scrollTop > 0')
                area.evaluate('(el) => { el.scrollTop = 0; }')
                page.screenshot(path=str(OUT / f'{"before" if baseline else "after"}-{section}-{width}.png'), animations='disabled')
                # Resize accounts for wrapping of heading/actions and mobile paginator.
                page.set_viewport_size({'width': width, 'height': height + 100})
                page.wait_for_function('(old) => document.querySelector(".table-wrap").clientHeight > old', arg=geometry['height'])
                page.set_viewport_size({'width': width, 'height': height})
            assert not errors, errors
            assert not fixture.writes
            ctx.close()
        browser.close()
    (OUT / ('before.json' if baseline else 'after.json')).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    run('--baseline' in sys.argv)
