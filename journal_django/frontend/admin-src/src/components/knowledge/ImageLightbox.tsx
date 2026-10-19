import { createContext, useCallback, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import * as RadixDialog from '@radix-ui/react-dialog';
import { imageUrl } from '../../lib/knowledge';

/**
 * Просмотр картинки в полном размере — поверх той же страницы, без ухода в
 * новую вкладку.
 *
 * Почему поверх страницы: статью читают, а не изучают картинку отдельно от
 * текста. Новая вкладка теряет место, где читатель остановился, и возвращаться
 * приходится вручную. Здесь Esc или клик мимо возвращают ровно туда же.
 *
 * Состояние живёт в провайдере, а не в самой картинке: DocumentView
 * перерисовывается при каждом обновлении данных документа, и локальное
 * состояние внутри отрендеренного дерева закрывало бы просмотрщик само собой.
 *
 * Radix Dialog, а не своя подложка: фокус-ловушка, Esc, блокировка прокрутки
 * фона и aria-modal — уже в нём. Штатный Dialog проекта не подходит: у него
 * шапка с заголовком и поля вокруг тела, а картинке нужен весь экран.
 */

export interface LightboxImage {
  id: number;
  /** Подпись из документа: заголовок окна и alt полноразмерной картинки. */
  alt: string;
}

const LightboxContext = createContext<((image: LightboxImage) => void) | null>(null);

/** Открыть просмотрщик; null — если картинка рисуется вне провайдера. */
export function useImageLightbox() {
  return useContext(LightboxContext);
}

export function ImageLightboxProvider({ children }: { children: ReactNode }) {
  const [image, setImage] = useState<LightboxImage | null>(null);
  const open = useCallback((next: LightboxImage) => setImage(next), []);

  return (
    <LightboxContext.Provider value={open}>
      {children}
      <RadixDialog.Root open={image !== null} onOpenChange={(next) => { if (!next) setImage(null); }}>
        <RadixDialog.Portal>
          {/* Содержимое вложено в подложку, как у .modal-overlay > .modal:
              подложка остаётся отдельной кликабельной площадью, и нажатие мимо
              картинки закрывает просмотрщик. */}
          <RadixDialog.Overlay className="kb-lightbox__overlay">
            <RadixDialog.Content className="kb-lightbox" aria-describedby={undefined}>
              <RadixDialog.Title className="sr-only">
                {image?.alt || 'Изображение'}
              </RadixDialog.Title>
              <RadixDialog.Close className="kb-lightbox__close" aria-label="Закрыть">
                ×
              </RadixDialog.Close>
              {image && (
                /* Оригинал, а не сжатый вариант: ради него просмотрщик и
                   открывают. Запрос уходит только при открытии — в статье
                   остаётся лёгкая версия. */
                <img
                  className="kb-lightbox__img"
                  src={imageUrl(image.id, 'original')}
                  alt={image.alt}
                />
              )}
            </RadixDialog.Content>
          </RadixDialog.Overlay>
        </RadixDialog.Portal>
      </RadixDialog.Root>
    </LightboxContext.Provider>
  );
}
