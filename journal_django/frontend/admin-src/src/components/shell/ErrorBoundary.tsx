import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props { children: ReactNode }
interface State { hasError: boolean; message?: string }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error?.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[admin] render error:', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-fallback">
          <h2 className="error-fallback__title">Что-то пошло не так</h2>
          <p className="error-fallback__text">Страница не отрисовалась. Попробуйте перезагрузить.</p>
          {/* Текст ошибки под катом: без него о поломке можно сообщить только
              словами «не работает», а причина остаётся в консоли, куда никто
              не заглядывает. Свёрнуто, чтобы не пугать обычного пользователя. */}
          {this.state.message && (
            <details className="error-fallback__details">
              <summary>Подробности для разработчика</summary>
              <code>{this.state.message}</code>
            </details>
          )}
          <button
            type="button"
            className="btn-save error-fallback__btn"
            onClick={() => window.location.reload()}
          >
            Перезагрузить
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
