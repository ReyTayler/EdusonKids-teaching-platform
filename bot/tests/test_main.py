"""Сборка приложения: адрес Bot API."""


def test_custom_api_base(monkeypatch):
    """Когда api.telegram.org недоступен напрямую, бот ходит через свой адрес."""
    monkeypatch.setenv('BOT_TOKEN', '123:abc')
    import main
    monkeypatch.setattr(main, 'BOT_TOKEN', '123:abc')
    monkeypatch.setattr(main, 'TELEGRAM_API_BASE', 'https://relay.example')
    assert main.build_application().bot.base_url == 'https://relay.example/bot123:abc'


def test_default_api_base(monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', '123:abc')
    import main
    monkeypatch.setattr(main, 'BOT_TOKEN', '123:abc')
    monkeypatch.setattr(main, 'TELEGRAM_API_BASE', '')
    assert main.build_application().bot.base_url == 'https://api.telegram.org/bot123:abc'
