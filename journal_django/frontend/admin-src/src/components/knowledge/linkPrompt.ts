import type { Editor } from '@tiptap/react';

/**
 * Спросить адрес ссылки и повесить её на выделение.
 *
 * Через window.prompt — и это осознанный выбор, а не спешка: своя модалка
 * забирает фокус у редактора, из-за чего выделение схлопывается и ссылку
 * некуда вешать. Обходится это сохранением и восстановлением диапазона, что на
 * одно поле ввода выходит дороже, чем стоит. Промпт блокирующий: к моменту
 * возврата выделение на месте.
 *
 * Живёт отдельным модулем, потому что вызывается из двух мест — кнопки панели
 * и сочетания Ctrl+K.
 */
export function askLink(editor: Editor) {
  if (editor.isDestroyed) return;
  const current = (editor.getAttributes('link').href as string | undefined) ?? '';
  const href = window.prompt('Адрес ссылки (пусто — убрать ссылку):', current);
  if (href === null) return;                      // отмена — ничего не трогаем

  const chain = editor.chain().focus();
  if (!href.trim()) {
    chain.unsetLink().run();
    return;
  }
  // Человек пишет «example.com», а браузеру нужна схема: без неё адрес
  // считается относительным и ведёт внутрь админки.
  const value = href.trim();
  const normalized = /^(https?:\/\/|mailto:|\/)/.test(value) ? value : `https://${value}`;
  chain.extendMarkRange('link').setLink({ href: normalized }).run();
}
