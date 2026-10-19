import { stageTone } from '../../lib/stage-tone';

interface Props {
  /** Выбранный цвет в hex. null — цвет ещё не задан, ни один кружок не отмечен. */
  value: string | null;
  onChange: (color: string) => void;
  colors: readonly string[];
  'aria-label'?: string;
}

/**
 * Выбор цвета из фиксированного набора кружков.
 *
 * Пришёл на смену `<input type="color">` там, где цвет — свойство общей для всей
 * школы структуры (стадии воронки). Произвольный hex давал грязные и неотличимые
 * друг от друга оттенки на доске, а половина из них ещё и не держала контраст с
 * подписью. Набор подобран так, что `stageTone` для каждого цвета находит
 * читаемый цвет текста.
 *
 * Варианта «без цвета» здесь нет намеренно: пустой кружок в ряду читался как
 * белая колонка, а означал «тон по названию». Белый теперь обычный цвет набора.
 */
export function ColorSwatches({
  value, onChange, colors, 'aria-label': ariaLabel,
}: Props) {
  const current = value ? value.toUpperCase() : null;

  return (
    <div className="color-swatches" role="group" aria-label={ariaLabel}>
      {colors.map((hex) => {
        const active = current === hex.toUpperCase();
        return (
          <button
            key={hex}
            type="button"
            className={`color-swatches__item${active ? ' is-active' : ''}`}
            // Галочка красится тем же цветом, каким на этой заливке будет подпись
            // стадии: на светло-жёлтом кружке белая галочка не видна.
            style={{ background: hex, color: stageTone(hex, hex).ink }}
            aria-pressed={active}
            aria-label={`Цвет ${hex}`}
            title={hex}
            onClick={() => onChange(hex)}
          >
            {active && <CheckGlyph />}
          </button>
        );
      })}
    </div>
  );
}

function CheckGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}
