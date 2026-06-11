# MTProto Tunnel — Telegram Bypass for Censored VPS

Обход блокировок Telegram на VPS через MTProto-прокси с автоматическим
фолбэком на Tor SOCKS5.

## Как работает

```
┌──────────────┐     MTProto/Tor      ┌──────────────┐     MTProto      ┌──────────────┐
│  Твой VPS     │ ──────────────────→ │  Прокси / Tor  │ ──────────────→ │  Telegram DC  │
│  (заблокирован)│                     │  (не заблокирован)│               │               │
└──────────────┘                      └──────────────┘                 └──────────────┘
```

Приоритет транспортов:
1. **MTProto-прокси** (прямой туннель, быстро)
2. **Tor SOCKS5** (фолбэк, медленно но бесплатно)

## Установка

```bash
# Зависимости
pip install telethon cryptg python-socks

# Tor (опционально, для фолбэка)
apt install tor obfs4proxy
systemctl start tor
```

## Конфигурация

```bash
# MTProto прокси (обязательно)
export TG_PROXY_SERVER="100.100.100.100"
export TG_PROXY_PORT="443"
export TG_PROXY_SECRET="00...your_secret..."

# Токен бота от @BotFather (обязательно для send)
export TG_BOT_TOKEN="1234567890:ABCdef..."

# Tor SOCKS5 (опционально)
export TG_TOR_SOCKS="127.0.0.1:9050"
```

## Использование

```bash
# Проверить соединение (пробует все транспорты)
python3 mtproto_tunnel.py ping

# Отправить сообщение
python3 mtproto_tunnel.py send @username "Привет из заблокированного VPS!"

# Только через Tor
python3 mtproto_tunnel.py --tor ping

# Показать конфигурацию
python3 mtproto_tunnel.py status --test
```

## Интеграция с Hermes Agent

```bash
# Вариант 1: через Tor SOCKS5
hermes config set telegram.proxy_url "socks5://127.0.0.1:9050"

# Вариант 2: как external tool
# Добавить в Hermes custom tools вызов mtproto_tunnel.py send
```

## Формат секрета прокси

Секрет в `tg://proxy` ссылках — это URL-safe base64 от:
- 16 байт: случайный ключ
- 1 байт: разделитель
- Остальное: домен для FakeTLS (например, `ria.ru`)

Скрипт автоматически парсит и hex, и base64.

## Ограничения

- Работает только для Bot API (через MTProto-клиент telethon)
- Не заменяет HTTP/SOCKS5 прокси для произвольного трафика
- Требует `TG_BOT_TOKEN` от @BotFather
- Tor может требовать мосты (obfs4/snowflake) в сетях с жёсткой цензурой
