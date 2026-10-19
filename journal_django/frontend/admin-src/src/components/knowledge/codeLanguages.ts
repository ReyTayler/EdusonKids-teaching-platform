import { createLowlight } from 'lowlight';

/**
 * Языки блока кода.
 *
 * Список закрытый и совпадает с белым списком сервера (content.py:
 * ALLOWED_CODE_LANGUAGES). Полный highlight.js — это почти двести грамматик и
 * лишние сотни килобайт; в регламентах школы встречаются вот эти.
 */
export const CODE_LANGUAGES = [
  { value: '', label: 'Без подсветки' },
  { value: 'plaintext', label: 'Текст' },
  { value: 'javascript', label: 'JavaScript' },
  { value: 'typescript', label: 'TypeScript' },
  { value: 'python', label: 'Python' },
  { value: 'sql', label: 'SQL' },
  { value: 'bash', label: 'Bash' },
  { value: 'json', label: 'JSON' },
  { value: 'html', label: 'HTML' },
  { value: 'css', label: 'CSS' },
] as const;

export const CODE_LANGUAGE_VALUES = CODE_LANGUAGES
  .map((item) => item.value)
  .filter(Boolean) as string[];

export type Lowlight = ReturnType<typeof createLowlight>;

/**
 * Догрузить грамматики в существующий экземпляр.
 *
 * Грамматики отделены от ядра намеренно. Ядро lowlight крошечное и создаётся
 * сразу — иначе редактор пришлось бы пересобирать, когда подсветка приедет, а
 * пересборка сбрасывает курсор и историю правок прямо под руками у человека.
 * Тяжёлые грамматики догружаются в тот же экземпляр и начинают работать со
 * следующей перерисовки блока.
 */
export async function registerLanguages(lowlight: Lowlight): Promise<void> {
  const [
    javascript, typescript, python, sql, bash, json, xml, css, plaintext,
  ] = await Promise.all([
    import('highlight.js/lib/languages/javascript'),
    import('highlight.js/lib/languages/typescript'),
    import('highlight.js/lib/languages/python'),
    import('highlight.js/lib/languages/sql'),
    import('highlight.js/lib/languages/bash'),
    import('highlight.js/lib/languages/json'),
    import('highlight.js/lib/languages/xml'),
    import('highlight.js/lib/languages/css'),
    import('highlight.js/lib/languages/plaintext'),
  ]);

  lowlight.register('javascript', javascript.default);
  lowlight.register('typescript', typescript.default);
  lowlight.register('python', python.default);
  lowlight.register('sql', sql.default);
  lowlight.register('bash', bash.default);
  lowlight.register('json', json.default);
  // HTML в highlight.js разбирает грамматика xml.
  lowlight.register('html', xml.default);
  lowlight.register('css', css.default);
  lowlight.register('plaintext', plaintext.default);
}

/**
 * Экземпляр для читалки: ядро и грамматики целиком по требованию.
 *
 * Здесь, в отличие от редактора, статический импорт недопустим — DocumentView
 * подключён к каждой странице админки, и lowlight уехал бы в основной бандл.
 */
export async function loadHighlighter(): Promise<Lowlight> {
  const { createLowlight: create } = await import('lowlight');
  const lowlight = create();
  await registerLanguages(lowlight);
  return lowlight;
}
