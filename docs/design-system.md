# Design System (admin SPA)

Personality: **Linear × Stripe** (precision + sophistication). Cool slate palette, indigo accent (`#4F59F9` — KOTOKOD identity, unified with teacher SPA).

Единственный источник токенов: `journal_django/frontend/admin-src/src/styles/tokens.css`

## Токены

```
Spacing:  --space-1..--space-10  (4/8/12/16/20/24/32/40px — 4px grid)
Radius:   --r-sm/--r/--r-lg      (6/8/12px)
Surfaces: --bg2/--bg3/--bg4
Borders:  --border/--border2/--border-strong  (semi-transparent rgba)
Text:     --text/--text2/--text3/--text4      (4-level contrast)
Accent:   --accent/--accent-hover/--accent-soft  (#4F59F9 — см. tokens.css)
Status:   --success/--warning/--danger/--info
Shadows:  --shadow-modal/--shadow-popover
Fonts:    --font-sans (Inter), --font-display (Steppe), --font-mono (JetBrains Mono)
Motion:   --ease (cubic-bezier 0.25,1,0.5,1), --t-fast (150ms), --t-base (200ms)
```

## Запреты

| Anti-pattern | Что вместо |
|---|---|
| Native `<select>`, `<input type="date">`, `<input type="checkbox">` | `SelectInput`, `DateInput`, `Checkbox`, `Combobox` |
| Hardcoded цвета/радиусы | `var(--accent)`, `var(--r)` и т.д. |
| `!important` | Правильная specificity |
| `--accent2` (удалён) | Единственный `--accent` |
| Декоративные цвета для badges | Color только для смысла: success/warning/danger/info |
| `box-shadow: 0 25px 50px ...` | `var(--shadow-modal)` или borders |
| `transform: scale(0.97)` на active | Subtle `inset box-shadow` |
| Произвольные transitions | `var(--t-fast) var(--ease)` |
| `2px+` декоративные борды | `1px solid var(--border)` |
| `16px+` радиус на мелких элементах | 6-8px |
| `rgba(0,0,0,0.12)` | `color-mix(in oklab, var(--accent) 12%, transparent)` |
| Enum-строки руками («учится») | `ENROLLMENT_STATUS_LABELS` из `lib/labels.ts` |

## Обязательно

- **4px grid**: `--space-1..--space-10`
- **TLBR симметрия**: `padding: var(--space-4)`, не `12px 16px 8px 16px`
- **Typography**: заголовки `--font-display 600 -0.02em`, числа/id/даты `--font-mono tabular-nums`
- **Focus ring**: `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }`
- **Dark theme через токены**: не писать `[data-theme="dark"] .foo { color: #abc }`
- **Inline style** — только для `--dir-color` динамической переменной

## Custom form elements

- `SelectInput.tsx` — trigger + popover, keyboard nav (↑↓/Enter/Esc)
- `Combobox.tsx` — search-as-you-type (PaymentModal: ученик, направление, скидки)
- `DateInput.tsx` — calendar popover, month nav, Сегодня/Очистить (формы, PayrollPage, LessonEditor)
- `Checkbox.tsx` — SVG checkmark
- `DataTable` filter-row — `SelectInput` вместо native для колонок с `searchOptions`

## StatusBadge

Tone-based: `positive/negative/info/muted`  
учится→зелёный, отказался→красный, заморожен→синий, не учится→серый

## CSS-каскад (styles/index.css)

`tokens → base → shell → components → table → forms → modal → pages → overrides`  
`overrides.css` импортируется **последним** (focus rings, polish, dark-boost).
