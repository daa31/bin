# Binance P2P USDT/UAH Alert Bot

Telegram-бот отслеживает цену покупки USDT за UAH в Binance P2P и присылает уведомление, когда цена в зелёном стакане становится равной целевой цене пользователя или ниже.

По умолчанию цель: `44.9 UAH`.

## Возможности

- кнопочное меню в Telegram;
- отдельная целевая цена для каждого пользователя;
- фильтр минимальной суммы сделки в UAH, по умолчанию `2000`;
- фильтр способа оплаты: Monobank, PrivatBank, ПУМБ, А-Банк, Ощадбанк, Raiffeisen, izibank, Sportbank;
- быстрая проверка текущей цены;
- пауза и включение уведомлений;
- антиспам: одно и то же объявление не отправляется повторно;
- деплой на Railway через `Dockerfile`.

## Переменные окружения

Создай переменные на Railway:

```env
BOT_TOKEN=123456:telegram-bot-token
DEFAULT_TARGET_PRICE=44.9
DEFAULT_MIN_TRADE_AMOUNT=2000
CHECK_INTERVAL_SECONDS=60
BINANCE_ASSET=USDT
BINANCE_FIAT=UAH
BINANCE_TRADE_TYPE=BUY
DATA_FILE=data/users.json
```

`BINANCE_TRADE_TYPE=BUY` используется для зелёного стакана. Если в интерфейсе Binance нужна противоположная вкладка, поменяй значение на `SELL`.

## Локальный запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

Перед запуском укажи реальный `BOT_TOKEN`.

## Railway

Railway сам соберёт проект из `Dockerfile`. После подключения репозитория укажи переменные окружения и запусти сервис как worker.
