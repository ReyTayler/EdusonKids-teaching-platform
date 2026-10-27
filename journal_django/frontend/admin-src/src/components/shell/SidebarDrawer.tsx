import * as RadixDialog from '@radix-ui/react-dialog';
import { Sidebar } from './Sidebar';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Мобильная навигация: тот же сайдбар, что на широком экране, выезжает слева
 * поверх страницы (размещение как у KOTOKOD, оформление — Eduson). Один
 * компонент навигации на оба режима: разделы, группы, пользователь, тема и
 * «Выйти» не разъезжаются между десктопом и телефоном.
 *
 * Esc, клик по подложке, ловушку фокуса, возврат фокуса на кнопку меню и
 * блокировку прокрутки страницы берёт на себя Radix Dialog. Закрытие при
 * переходе в раздел — в AppShell (по смене pathname).
 */
export function SidebarDrawer({ open, onOpenChange }: Props) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="sidebar-drawer-overlay" />
        <RadixDialog.Content className="sidebar-drawer" aria-describedby={undefined}>
          <RadixDialog.Title className="sr-only">Меню разделов</RadixDialog.Title>
          <Sidebar onToggle={() => onOpenChange(false)} toggleLabel="Закрыть меню" />
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
