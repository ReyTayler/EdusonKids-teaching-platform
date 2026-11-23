import { useLayoutEffect, useRef, type ComponentPropsWithoutRef } from 'react';

/** Limits table scrolling to the space below its actual heading and above pagination. */
export function TableScrollArea({ className = '', ...props }: ComponentPropsWithoutRef<'div'>) {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const area = ref.current;
    if (!area || area.closest('[role="dialog"]')) return;
    const panel = area.parentElement;
    const page = area.closest<HTMLElement>('.app-page, .page');
    let frame = 0;
    const measure = () => {
      const top = area.getBoundingClientRect().top + window.scrollY;
      const footer = panel?.classList.contains('table-panel')
        ? panel.getBoundingClientRect().height - area.getBoundingClientRect().height
        : 0;
      const bottom = page ? parseFloat(getComputedStyle(page).paddingBottom) || 0 : 20;
      // Lower sections retain a usable scroll area even when they start below the fold.
      const height = Math.max(240, Math.floor((window.visualViewport?.height ?? window.innerHeight) - top - footer - bottom));
      const value = `${height}px`;
      if (area.style.getPropertyValue('--table-max-height') !== value) {
        area.style.setProperty('--table-max-height', value);
      }
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    if (panel) observer.observe(panel);
    if (page) {
      observer.observe(page);
      for (const child of page.children) observer.observe(child);
    }
    window.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('resize', schedule);
    measure();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      window.visualViewport?.removeEventListener('resize', schedule);
    };
  }, []);

  return <div {...props} ref={ref} className={`table-wrap ${className}`.trim()} />;
}
