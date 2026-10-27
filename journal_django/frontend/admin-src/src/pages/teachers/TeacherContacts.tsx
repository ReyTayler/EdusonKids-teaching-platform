import { useToast } from '../../components/ui/Toast';
import type { Teacher } from '../../lib/types';

interface Contact {
  label: string;
  /** То, что копируется, — значение как есть. */
  value: string | null;
  href: (value: string) => string;
  /** Что показать в строке: у ссылок срезаем протокол, чтобы влезал адрес. */
  display: (value: string) => string;
  /** Внешняя ссылка — в новой вкладке; mailto/tel — в той же. */
  external?: boolean;
  copied: string;
}

const stripProtocol = (url: string) => url.replace(/^https?:\/\//, '');
const asIs = (value: string) => value;

/**
 * Контакты в шапке карточки преподавателя: звонок, VK, email, телефон —
 * каждый с кнопкой копирования, чтобы быстро переслать родителю или коллеге.
 *
 * Стоят под чипами в основной колонке, а не карточками в правой: это
 * короткие строки, а отдельные рамки рядом с блоком Telegram растягивались
 * в пустые коробки. Копирование — тем же приёмом, что invite-ссылка в
 * «Учётках» (AccountsPage): clipboard + подтверждение тостом. Правка — в
 * общей форме «Редактировать».
 */
export function TeacherContacts({ teacher }: { teacher: Teacher }) {
  const { toast } = useToast();

  const contacts: Contact[] = [
    {
      label: 'Звонок', value: teacher.call_url, href: asIs, display: stripProtocol,
      external: true, copied: 'Ссылка на звонок скопирована',
    },
    {
      label: 'VK', value: teacher.vk_url, href: asIs, display: stripProtocol,
      external: true, copied: 'Ссылка на VK скопирована',
    },
    {
      label: 'Email', value: teacher.email, href: (v) => `mailto:${v}`, display: asIs,
      copied: 'Email скопирован',
    },
    {
      label: 'Телефон', value: teacher.phone, href: (v) => `tel:${v.replace(/[^\d+]/g, '')}`,
      display: asIs, copied: 'Телефон скопирован',
    },
  ];

  const copy = (c: Contact) => {
    if (!c.value) return;
    navigator.clipboard.writeText(c.value).then(
      () => toast(c.copied, 'ok'),
      () => toast('Не удалось скопировать', 'error'),
    );
  };

  return (
    <dl className="contacts">
      {contacts.map((c) => (
        <div key={c.label} className="contacts__row">
          <dt className="contacts__label">{c.label}</dt>
          <dd className="contacts__value">
            {c.value ? (
              <>
                {/* noopener/noreferrer: чужой сайт не получает window.opener и адрес карточки. */}
                <a
                  className="contacts__link"
                  href={c.href(c.value)}
                  title={c.value}
                  {...(c.external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                >
                  {c.display(c.value)}
                </a>
                <button
                  type="button"
                  className="contacts__copy"
                  onClick={() => copy(c)}
                  title={`Скопировать: ${c.label}`}
                  aria-label={`Скопировать: ${c.label}`}
                >
                  <CopyGlyph />
                </button>
              </>
            ) : (
              <span className="contacts__empty">не указан</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function CopyGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="8.5" y="8.5" width="12" height="12" rx="2" />
      <path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3" />
    </svg>
  );
}
