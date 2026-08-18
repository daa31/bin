# Binance P2P USDT/UAH Alert Bot

Telegram-бот отслеживает цену USDT за UAH в Binance P2P и присылает уведомление, когда цена в выбранном стакане доходит до цели.

По умолчанию зелёный стакан: `44.9 UAH` и сумма `от 2000 UAH`.
По умолчанию красный стакан: `45.4 UAH` и сумма `15000 UAH`.

## Возможности

- кнопочное меню в Telegram;
- стартовый выбор стакана после `/start` с кнопкой возврата к выбору;
- постоянный активный мониторинг после `/start`;
- команды `.stop` и `.start` для остановки и возобновления постоянного анализа без сброса настроек;
- выбор зелёного или красного стакана для каждого пользователя;
- ручная проверка текущей цены отдельно для зелёного и красного стакана;
- отдельная целевая цена для каждого пользователя;
- отдельный фильтр минимальной суммы сделки в UAH;
- для красного стакана скрываются объявления, где в описании есть ФОП/физ/IBAN/ибан/банка или условия вроде "на карту не отправляю";
- фильтр способа оплаты: Monobank, PrivatBank, ПУМБ, А-Банк, Ощадбанк, Raiffeisen, izibank, Sportbank;
- антиспам: одно и то же объявление не отправляется повторно;
- деплой на Railway через `Dockerfile`.

## Переменные окружения

Создай переменные на Railway:

```env
BOT_TOKEN=123456:telegram-bot-token
DEFAULT_TARGET_PRICE=44.9
DEFAULT_MIN_TRADE_AMOUNT=2000
DEFAULT_RED_TARGET_PRICE=45.4
DEFAULT_RED_MIN_TRADE_AMOUNT=15000
CHECK_INTERVAL_SECONDS=60
BINANCE_ASSET=USDT
BINANCE_FIAT=UAH
BINANCE_TRADE_TYPE=BUY
BINANCE_RED_TRADE_TYPE=SELL
DATA_FILE=data/users.json
```

`BINANCE_TRADE_TYPE=BUY` используется для зелёного стакана, `BINANCE_RED_TRADE_TYPE=SELL` — для красного.

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
