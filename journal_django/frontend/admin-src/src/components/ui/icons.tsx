/**
 * Inline-SVG иконки. Внешних спрайтов и CDN нет — CSP `script-src 'self'`
 * и `img-src 'self'` не пропустят ни то, ни другое.
 *
 * Цвет — всегда `currentColor`: иконка наследует цвет текста и работает
 * в обеих темах без отдельных правил.
 */
interface IconProps {
  size?: number;
  className?: string;
}

export function TelegramIcon({ size = 16, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path d="M21.9 4.3 18.7 20c-.2 1-.9 1.3-1.8.8l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1L18.2 6c.4-.4-.1-.6-.6-.2L6.2 12.9l-5-1.6c-1-.3-1-1 .2-1.5l19.4-7.5c.8-.3 1.5.2 1.2 2z" />
    </svg>
  );
}
