from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
BINANCE_P2P_ADV_DETAIL_URL = "https://p2p.binance.com/bapi/c2c/v2/public/c2c/adv/detail"
BINANCE_P2P_MAX_PAGES = 25
PAYMENT_METHODS = {
    "Monobank": "Monobank",
    "PrivatBank": "PrivatBank",
    "PUMBBank": "ПУМБ",
    "ABank": "А-Банк",
    "Oschadbank": "Ощадбанк",
    "RaiffeisenBankAval": "Raiffeisen",
    "izibank": "izibank",
    "Sportbank": "Sportbank",
}
BOOK_GREEN = "green"
BOOK_RED = "red"
BOOK_TITLES = {
    BOOK_GREEN: "зелёный стакан",
    BOOK_RED: "красный стакан",
}
BOOK_EMOJI = {
    BOOK_GREEN: "🟢",
    BOOK_RED: "🔴",
}
RED_BLOCKED_TERMS = (
    "фоп",
    "fop",
    "физ",
    "фіз",
    "iban",
    "іban",
    "ибан",
    "банка",
    "банки",
    "конверт",
    "konvert",
    "разных отправ",
    "різних відправ",
    "разные отправ",
    "різні відправ",
    "разных платель",
    "різних платник",
    "пополнения",
    "поповнення",
    "api-ключ",
    "api ключ",
    "api-key",
    "апі-ключ",
    "апі ключ",
    "довірених осіб",
    "довірних осіб",
    "довірених лиць",
    "довірних лиць",
    "доверенных лиц",
    "дов особи",
    "дов. особи",
    "от 3 лица",
    "від 3 особи",
    "від 3 особ",
    "3 лица",
    "3 особи",
    "3 особ",
    "третьих лиц",
    "третіх осіб",
    "чеки та квитанції",
    "не надсила",
    "не висила",
    "не отправля",
    "багатьма переказ",
    "многими перевод",
    "багато платеж",
    "много платеж",
    "декілька платеж",
    "кілька платеж",
    "несколько платеж",
    "поділена на кілька",
    "поделена на несколько",
    "оплата може бути поділена",
    "оплата может быть поделена",
    "фінансові переказ",
    "финансовые перевод",
    "комісію сплачує клієнт",
    "комиссию платит клиент",
    "1-3 платеж",
    "2-3 платеж",
    "3-15 транзак",
    "платежі від довір",
    "платежи от довер",
    "через ліміти",
    "через лимиты",
    "з лімітами",
    "с лимитами",
    "декількома транзак",
    "несколькими транзак",
    "декілька транзак",
    "несколько транзак",
    "не можу надати",
    "не могу предоставить",
    "скрін зарахування",
    "скрин зачисления",
    "зарахування у виписці",
    "зачисления в выписке",
    "не отправ",
    "не відправ",
)
BTN_SETTINGS = "🎯 Моя цель"
BTN_BOOK = "📊 Стакан"
BTN_CHECK_GREEN = "🟢 Цена зелёный"
BTN_CHECK_RED = "🔴 Цена красный"
BTN_CHANGE_PRICE = "✏️ Изменить цену"
BTN_PAYMENTS = "🏦 Банк"
BTN_AMOUNT = "💰 Сумма"
BTN_BACK = "⬅️ Назад"


@dataclass
class Config:
    bot_token: str
    default_target_price: Decimal
    default_min_trade_amount: Decimal
    default_red_target_price: Decimal
    default_red_min_trade_amount: Decimal
    check_interval_seconds: int
    asset: str
    fiat: str
    trade_type: str
    red_trade_type: str
    data_file: Path


class UserStore:
    def __init__(self, path: Path, config: Config) -> None:
        self.path = path
        self.config = config
        self._lock = asyncio.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    async def load(self) -> None:
        async with self._lock:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._data = {}
                return
            self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    async def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _default_user(self) -> dict[str, Any]:
        return {
            "book": BOOK_GREEN,
            "target": str(self.config.default_target_price),
            "min_trade_amount": str(self.config.default_min_trade_amount),
            "green_target": str(self.config.default_target_price),
            "green_min_trade_amount": str(self.config.default_min_trade_amount),
            "red_target": str(self.config.default_red_target_price),
            "red_min_trade_amount": str(self.config.default_red_min_trade_amount),
            "awaiting_price": False,
            "awaiting_amount": False,
            "payment_methods": [],
            "last_alert_adv_no": None,
        }

    def _normalize_user(self, user: dict[str, Any]) -> dict[str, Any]:
        defaults = self._default_user()
        changed = False
        for key, value in defaults.items():
            if key not in user:
                user[key] = value
                changed = True
        if user.get("book") not in BOOK_TITLES:
            user["book"] = BOOK_GREEN
            changed = True
        book = str(user["book"])
        if "green_target" not in user:
            user["green_target"] = str(user.get("target", self.config.default_target_price))
            changed = True
        if "green_min_trade_amount" not in user:
            user["green_min_trade_amount"] = str(user.get("min_trade_amount", self.config.default_min_trade_amount))
            changed = True
        if "red_target" not in user:
            user["red_target"] = str(self.config.default_red_target_price)
            changed = True
        if "red_min_trade_amount" not in user:
            user["red_min_trade_amount"] = str(self.config.default_red_min_trade_amount)
            changed = True
        target_key = f"{book}_target"
        amount_key = f"{book}_min_trade_amount"
        if user.get("target") != user.get(target_key):
            user["target"] = user[target_key]
            changed = True
        if user.get("min_trade_amount") != user.get(amount_key):
            user["min_trade_amount"] = user[amount_key]
            changed = True
        return user if changed else user

    async def ensure_user(self, user_id: int) -> dict[str, Any]:
        async with self._lock:
            key = str(user_id)
            if key not in self._data:
                self._data[key] = self._default_user()
                await self._save_locked()
            else:
                before = dict(self._data[key])
                self._normalize_user(self._data[key])
                if self._data[key] != before:
                    await self._save_locked()
            return dict(self._data[key])

    async def update_user(self, user_id: int, **values: Any) -> dict[str, Any]:
        async with self._lock:
            key = str(user_id)
            user = self._data.setdefault(key, self._default_user())
            self._normalize_user(user)
            user.update(values)
            await self._save_locked()
            return dict(user)

    async def all_users(self) -> dict[int, dict[str, Any]]:
        async with self._lock:
            changed = False
            for data in self._data.values():
                before = dict(data)
                self._normalize_user(data)
                changed = changed or data != before
            if changed:
                await self._save_locked()
            return {int(user_id): dict(data) for user_id, data in self._data.items()}


class BinanceP2P:
    def __init__(self, session: aiohttp.ClientSession, config: Config) -> None:
        self.session = session
        self.config = config

    async def best_offer(
        self,
        trade_type: str,
        pay_types: list[str] | None = None,
        min_trade_amount: Decimal | None = None,
        exclude_red_descriptions: bool = False,
        amount_must_fit_limits: bool = False,
    ) -> dict[str, Any] | None:
        pay_types = pay_types or []
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 Binance price alert bot",
        }

        for page in range(1, BINANCE_P2P_MAX_PAGES + 1):
            payload = {
                "fiat": self.config.fiat,
                "page": page,
                "rows": 20,
                "tradeType": trade_type,
                "asset": self.config.asset,
                "payTypes": pay_types,
                "publisherType": None,
            }
            async with self.session.post(BINANCE_P2P_URL, json=payload, headers=headers, timeout=20) as response:
                response.raise_for_status()
                body = await response.json()

            rows = body.get("data") or []
            if not rows:
                return None

            for row in rows:
                adv = row["adv"]
                adv_text = adv
                if exclude_red_descriptions:
                    if has_blocked_red_payment_method(adv):
                        continue
                    detail = await self.adv_detail(str(adv["advNo"]))
                    if detail is not None:
                        adv_text = {**adv, **detail}
                    if has_blocked_red_payment_method(adv_text):
                        continue
                    if has_blocked_red_description(adv_text):
                        continue
                min_amount = decimal_or_none(adv.get("minSingleTransAmount"))
                max_amount = decimal_or_none(adv.get("dynamicMaxSingleTransAmount") or adv.get("maxSingleTransAmount"))
                if min_trade_amount is not None and min_trade_amount > 0:
                    if amount_must_fit_limits:
                        if (
                            min_amount is None
                            or max_amount is None
                            or not (min_amount <= min_trade_amount <= max_amount)
                        ):
                            continue
                    elif min_amount is None or min_amount < min_trade_amount:
                        continue

                advertiser = row["advertiser"]
                return {
                    "adv_no": adv["advNo"],
                    "price": Decimal(str(adv["price"])),
                    "asset": adv.get("asset", self.config.asset),
                    "fiat": adv.get("fiatUnit", self.config.fiat),
                    "min_amount": adv.get("minSingleTransAmount"),
                    "max_amount": adv.get("dynamicMaxSingleTransAmount") or adv.get("maxSingleTransAmount"),
                    "available": adv.get("surplusAmount"),
                    "merchant": advertiser.get("nickName") or "Binance P2P",
                    "orders": advertiser.get("monthOrderCount"),
                    "finish_rate": advertiser.get("monthFinishRate"),
                    "payment_methods": [
                        method.get("identifier") or method.get("payType")
                        for method in adv.get("tradeMethods", [])
                        if method.get("identifier") or method.get("payType")
                    ],
                    "book": BOOK_RED if trade_type == self.config.red_trade_type else BOOK_GREEN,
                    "description": clean_description_text(adv_text),
                    "link": binance_web_url(self.config, trade_type, pay_types),
                }

        return None

    async def adv_detail(self, adv_no: str) -> dict[str, Any] | None:
        headers = {"User-Agent": "Mozilla/5.0 Binance price alert bot"}
        async with self.session.get(
            BINANCE_P2P_ADV_DETAIL_URL,
            params={"advNo": adv_no},
            headers=headers,
            timeout=20,
        ) as response:
            if response.status >= 400:
                logging.warning("Binance adv detail failed for %s: HTTP %s", adv_no, response.status)
                return None
            body = await response.json()

        data = body.get("data")
        if isinstance(data, dict):
            return data
        return None


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set BOT_TOKEN environment variable.")

    trade_type = os.getenv("BINANCE_TRADE_TYPE", "BUY").upper()
    red_trade_type = os.getenv("BINANCE_RED_TRADE_TYPE", opposite_trade_type(trade_type)).upper()

    return Config(
        bot_token=token,
        default_target_price=parse_price(os.getenv("DEFAULT_TARGET_PRICE", "44.9")),
        default_min_trade_amount=parse_amount(os.getenv("DEFAULT_MIN_TRADE_AMOUNT", "2000")),
        default_red_target_price=parse_price(os.getenv("DEFAULT_RED_TARGET_PRICE", "45.4")),
        default_red_min_trade_amount=parse_amount(os.getenv("DEFAULT_RED_MIN_TRADE_AMOUNT", "15000")),
        check_interval_seconds=max(15, int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))),
        asset=os.getenv("BINANCE_ASSET", "USDT").upper(),
        fiat=os.getenv("BINANCE_FIAT", "UAH").upper(),
        trade_type=trade_type,
        red_trade_type=red_trade_type,
        data_file=Path(os.getenv("DATA_FILE", "data/users.json")),
    )


def parse_price(value: str) -> Decimal:
    normalized = value.strip().replace(",", ".")
    try:
        price = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Цена должна быть числом, например 44.9") from exc
    if price <= 0:
        raise ValueError("Цена должна быть больше нуля")
    return price.quantize(Decimal("0.01"))


def parse_amount(value: str) -> Decimal:
    normalized = value.strip().replace(",", ".").replace(" ", "")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Сумма должна быть числом, например 2000") from exc
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return amount.quantize(Decimal("1"))


def opposite_trade_type(trade_type: str) -> str:
    return "SELL" if trade_type.upper() == "BUY" else "BUY"


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def red_description_text(adv: dict[str, Any]) -> str:
    fields = (
        "remarks",
        "remark",
        "description",
        "advRemark",
        "tradeRemark",
        "payTerm",
    )
    return " ".join(str(adv.get(field) or "") for field in fields).lower()


def normalize_red_description(description: str) -> str:
    normalized = " ".join(description.lower().split())
    return (
        normalized
        .replace("o", "о")
        .replace("0", "о")
        .replace("i", "і")
        .replace("ı", "і")
    )


def clean_description_text(adv: dict[str, Any]) -> str:
    fields = (
        "remarks",
        "remark",
        "description",
        "advRemark",
        "tradeRemark",
        "payTerm",
    )
    parts = [str(adv.get(field) or "").strip() for field in fields]
    return "\n".join(part for part in parts if part)


def has_blocked_red_description(adv: dict[str, Any]) -> bool:
    description = normalize_red_description(red_description_text(adv))
    if any(normalize_red_description(term) in description for term in RED_BLOCKED_TERMS):
        return True
    if ("квитанц" in description or "квитанці" in description) and (
        "не предостав" in description or "не нада" in description or "не можу нада" in description
    ):
        return True
    if ("транзакц" in description or "транзакці" in description) and (
        "декіль" in description or "несколь" in description or "3-15" in description or "через ліміт" in description
    ):
        return True
    if ("довірен" in description or "доверенн" in description) and (
        "особ" in description or "лиц" in description
    ):
        return True
    if ("скрін" in description or "скрин" in description) and (
        "зарахуван" in description or "зачислен" in description or "виписц" in description or "выписк" in description
    ):
        return True
    if ("разн" in description or "різн" in description) and (
        "отправител" in description or "відправник" in description or "платель" in description or "платник" in description
    ):
        return True
    if ("пополнен" in description or "поповнен" in description) and (
        "сумм" in description or "грн" in description
    ):
        return True
    return "карт" in description and "не" in description and (
        "отправ" in description or "відправ" in description
    )


def has_blocked_red_payment_method(adv: dict[str, Any]) -> bool:
    methods = [
        str(method.get("identifier") or method.get("payType") or "").lower()
        for method in adv.get("tradeMethods", [])
        if isinstance(method, dict)
    ]
    return any("iban" in method for method in methods)


def user_book(user: dict[str, Any]) -> str:
    book = str(user.get("book", BOOK_GREEN))
    return book if book in BOOK_TITLES else BOOK_GREEN


def book_title(user_or_book: dict[str, Any] | str) -> str:
    book = user_book(user_or_book) if isinstance(user_or_book, dict) else user_or_book
    return BOOK_TITLES.get(book, BOOK_TITLES[BOOK_GREEN])


def book_emoji(book: str) -> str:
    return BOOK_EMOJI.get(book, BOOK_EMOJI[BOOK_GREEN])


def user_trade_type(user: dict[str, Any], config: Config) -> str:
    return config.red_trade_type if user_book(user) == BOOK_RED else config.trade_type


def user_price_direction(user: dict[str, Any]) -> str:
    return "или выше" if user_book(user) == BOOK_RED else "или ниже"


def is_target_reached(book: str, price: Decimal, target: Decimal) -> bool:
    if book == BOOK_RED:
        return price >= target
    return price <= target


def default_values_for_book(book: str, config: Config) -> tuple[Decimal, Decimal]:
    if book == BOOK_RED:
        return config.default_red_target_price, config.default_red_min_trade_amount
    return config.default_target_price, config.default_min_trade_amount


def user_settings_text(user: dict[str, Any]) -> str:
    amount_label = "Сумма сделки" if user_book(user) == BOOK_RED else "Сумма"
    amount_prefix = "" if user_book(user) == BOOK_RED else "от "
    return (
        f"Стакан: <b>{book_title(user)}</b>\n"
        f"Цель: <b>{user['target']} UAH</b> {user_price_direction(user)}\n"
        f"{amount_label}: <b>{amount_prefix}{user_min_trade_amount(user)} UAH</b>\n"
        f"Банк: <b>{payment_title(user_payment_methods(user))}</b>"
    )


def user_values_for_book(user: dict[str, Any], book: str, config: Config) -> tuple[Decimal, Decimal]:
    default_target, default_min_trade_amount = default_values_for_book(book, config)
    target = parse_price(str(user.get(f"{book}_target", default_target)))
    min_trade_amount = parse_amount(str(user.get(f"{book}_min_trade_amount", default_min_trade_amount)))
    return target, min_trade_amount


def user_min_trade_amount(user: dict[str, Any]) -> Decimal:
    return parse_amount(str(user.get("min_trade_amount", "2000")))


def binance_web_url(
    config: Config,
    trade_type: str,
    pay_types: list[str] | None = None,
    trans_amount: Decimal | None = None,
) -> str:
    base = (
        f"https://p2p.binance.com/ru/trade/"
        f"{trade_type.lower()}/{config.asset}?fiat={config.fiat}"
    )
    params = []
    if pay_types:
        params.append(f"payment={','.join(pay_types)}")
    if trans_amount is not None and trans_amount > 0:
        params.append(f"amount={trans_amount.quantize(Decimal('1'))}")
    if not params:
        return base
    return f"{base}&{'&'.join(params)}"


def user_payment_methods(user: dict[str, Any]) -> list[str]:
    methods = user.get("payment_methods")
    if not isinstance(methods, list):
        return []
    return [method for method in methods if method in PAYMENT_METHODS]


def payment_title(methods: list[str]) -> str:
    if not methods:
        return "все банки"
    return ", ".join(PAYMENT_METHODS[method] for method in methods)


def main_keyboard(user: dict[str, Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_CHECK_GREEN, callback_data=f"price_now:{BOOK_GREEN}"),
                InlineKeyboardButton(text=BTN_CHECK_RED, callback_data=f"price_now:{BOOK_RED}"),
            ],
            [
                InlineKeyboardButton(text=BTN_SETTINGS, callback_data="settings"),
                InlineKeyboardButton(text=BTN_BOOK, callback_data="book"),
            ],
            [
                InlineKeyboardButton(text=BTN_CHANGE_PRICE, callback_data="change_price"),
                InlineKeyboardButton(text=BTN_PAYMENTS, callback_data="payments"),
            ],
            [
                InlineKeyboardButton(text=BTN_AMOUNT, callback_data="amount"),
            ],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="book")],
        ]
    )


def bottom_keyboard(user: dict[str, Any] | None = None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CHECK_GREEN), KeyboardButton(text=BTN_CHECK_RED)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_CHANGE_PRICE), KeyboardButton(text=BTN_PAYMENTS)],
            [KeyboardButton(text=BTN_AMOUNT)],
            [KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери действие",
    )


def book_keyboard(selected: str | None = None, show_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
            [
                InlineKeyboardButton(
                    text=("✅ " if selected == BOOK_GREEN else "") + "🟢 Зелёный",
                    callback_data=f"book:set:{BOOK_GREEN}",
                ),
                InlineKeyboardButton(
                    text=("✅ " if selected == BOOK_RED else "") + "🔴 Красный",
                    callback_data=f"book:set:{BOOK_RED}",
                ),
            ],
    ]
    if show_back:
        rows.append([InlineKeyboardButton(text=BTN_BACK, callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def presets_keyboard(book: str = BOOK_GREEN) -> InlineKeyboardMarkup:
    first_row = [
        InlineKeyboardButton(text="44.50", callback_data="preset:44.50"),
        InlineKeyboardButton(text="44.90", callback_data="preset:44.90"),
        InlineKeyboardButton(text="45.20", callback_data="preset:45.20"),
    ]
    if book == BOOK_RED:
        first_row = [
            InlineKeyboardButton(text="45.40", callback_data="preset:45.40"),
            InlineKeyboardButton(text="45.60", callback_data="preset:45.60"),
            InlineKeyboardButton(text="46.00", callback_data="preset:46.00"),
        ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            first_row,
            [InlineKeyboardButton(text="⌨️ Ввести свою", callback_data="custom_price")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="settings")],
        ]
    )


def amount_presets_keyboard(book: str = BOOK_GREEN) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="1000 грн", callback_data="amount_preset:1000"),
            InlineKeyboardButton(text="2000 грн", callback_data="amount_preset:2000"),
        ],
        [
            InlineKeyboardButton(text="5000 грн", callback_data="amount_preset:5000"),
            InlineKeyboardButton(text="10000 грн", callback_data="amount_preset:10000"),
        ],
    ]
    if book == BOOK_RED:
        rows = [
            [
                InlineKeyboardButton(text="5000 грн", callback_data="amount_preset:5000"),
                InlineKeyboardButton(text="10000 грн", callback_data="amount_preset:10000"),
            ],
            [
                InlineKeyboardButton(text="15000 грн", callback_data="amount_preset:15000"),
                InlineKeyboardButton(text="25000 грн", callback_data="amount_preset:25000"),
            ],
        ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *rows,
            [InlineKeyboardButton(text="⌨️ Ввести свою", callback_data="custom_amount")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="settings")],
        ]
    )


def payments_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ 🏦 Все банки" if not selected else "🏦 Все банки", callback_data="pay:all")]]
    methods = list(PAYMENT_METHODS.items())
    for index in range(0, len(methods), 2):
        row = []
        for code, title in methods[index : index + 2]:
            prefix = "✅ " if code in selected else ""
            row.append(InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"pay:toggle:{code}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def offer_text(
    offer: dict[str, Any],
    target: Decimal | None = None,
    trans_amount: Decimal | None = None,
) -> str:
    filter_lines = []
    if target is not None:
        filter_lines.append(f"<b>Твоя цель:</b> <b>{target} {offer['fiat']}</b>")
    if trans_amount is not None:
        if offer.get("book") == BOOK_RED:
            filter_lines.append(f"<b>Сумма сделки:</b> <b>{trans_amount} {offer['fiat']}</b>")
        else:
            filter_lines.append(f"<b>Сумма поиска:</b> <b>от {trans_amount} {offer['fiat']}</b>")
    filter_text = "\n".join(filter_lines)
    filter_block = f"\n{filter_text}\n" if filter_text else "\n"
    orders = offer.get("orders") or "-"
    finish_rate = offer.get("finish_rate")
    if finish_rate is not None:
        try:
            finish_rate = f"{Decimal(str(finish_rate)) * 100:.0f}%"
        except InvalidOperation:
            finish_rate = "-"
    else:
        finish_rate = "-"
    methods = [
        PAYMENT_METHODS.get(method, method)
        for method in offer.get("payment_methods", [])
        if method
    ]
    methods_line = ", ".join(methods) if methods else "-"
    book = offer.get("book", BOOK_GREEN)
    description_block = ""
    if book == BOOK_RED:
        description = str(offer.get("description") or "").strip() or "-"
        description_block = f"<b>Описание:</b> {html.escape(description)}\n"

    return (
        f"{book_emoji(book)} <b>{offer['asset']}/{offer['fiat']}: {offer['price']} {offer['fiat']}</b>"
        f"{filter_block}\n"
        f"<b>Стакан:</b> <b>{book_title(book)}</b>\n"
        f"<b>Продавец:</b> <b>{html.escape(str(offer['merchant']))}</b>\n"
        f"<b>Лимиты:</b> {offer.get('min_amount') or '-'} - {offer.get('max_amount') or '-'} {offer['fiat']}\n"
        f"<b>Доступно:</b> {offer.get('available') or '-'} {offer['asset']}\n"
        f"<b>Оплата:</b> {html.escape(methods_line)}\n"
        f"{description_block}"
        f"<b>Сделок за месяц:</b> {orders}, <b>завершение:</b> {finish_rate}\n\n"
        f"<a href=\"{offer['link']}\">Открыть объявление на Binance P2P</a>\n"
        f"<b>ID объявления:</b> <code>{offer['adv_no']}</code>"
    )


def alert_text(offer: dict[str, Any], target: Decimal, trans_amount: Decimal) -> str:
    direction = "или выше" if offer.get("book") == BOOK_RED else "или ниже"
    return f"🔥 <b>Цена дошла до цели {direction}</b>\n\n" + offer_text(offer, target, trans_amount)


async def send_current_price(
    bot: Bot,
    chat_id: int,
    p2p: BinanceP2P,
    user: dict[str, Any],
    book: str,
) -> None:
    if book not in BOOK_TITLES:
        await bot.send_message(chat_id, "Неизвестный стакан.")
        return

    pay_types = user_payment_methods(user)
    target, min_trade_amount = user_values_for_book(user, book, p2p.config)
    trade_type = p2p.config.red_trade_type if book == BOOK_RED else p2p.config.trade_type
    offer = await p2p.best_offer(
        trade_type,
        pay_types,
        min_trade_amount,
        exclude_red_descriptions=book == BOOK_RED,
        amount_must_fit_limits=book == BOOK_RED,
    )
    if offer is None:
        await bot.send_message(
            chat_id,
            f"Сейчас Binance не вернул объявления под фильтр: {book_title(book)}, "
            f"{'сумма сделки' if book == BOOK_RED else 'сумма от'} {min_trade_amount} "
            f"{p2p.config.fiat}, банк {payment_title(pay_types)}.",
        )
        return

    await bot.send_message(
        chat_id,
        offer_text(offer, target, min_trade_amount),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔗 Открыть Binance", url=offer["link"])]]
        ),
        disable_web_page_preview=True,
    )


async def watch_prices(bot: Bot, store: UserStore, p2p: BinanceP2P, interval: int) -> None:
    while True:
        try:
            users = await store.all_users()
            offers_by_filter: dict[tuple[str, ...], dict[str, Any] | None] = {}
            for chat_id, user in users.items():
                book = user_book(user)
                pay_types = user_payment_methods(user)
                trans_amount = user_min_trade_amount(user)
                trade_type = user_trade_type(user, p2p.config)
                filter_key = (book, trade_type, *pay_types, f"amount:{trans_amount}")
                if filter_key not in offers_by_filter:
                    offers_by_filter[filter_key] = await p2p.best_offer(
                        trade_type,
                        pay_types,
                        trans_amount,
                        exclude_red_descriptions=book == BOOK_RED,
                        amount_must_fit_limits=book == BOOK_RED,
                    )
                offer = offers_by_filter[filter_key]
                if offer is not None:
                    target = parse_price(str(user.get("target", p2p.config.default_target_price)))
                    adv_no = offer["adv_no"]
                    price = offer["price"]
                    if is_target_reached(book, price, target) and user.get("last_alert_adv_no") != adv_no:
                        await bot.send_message(
                            chat_id,
                            alert_text(offer, target, trans_amount),
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[[InlineKeyboardButton(text="🔗 Открыть Binance", url=offer["link"])]]
                            ),
                            disable_web_page_preview=True,
                        )
                        await store.update_user(chat_id, last_alert_adv_no=adv_no)
                    elif not is_target_reached(book, price, target) and user.get("last_alert_adv_no") is not None:
                        await store.update_user(chat_id, last_alert_adv_no=None)
        except Exception:
            logging.exception("Price watcher failed")

        await asyncio.sleep(interval)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    store = UserStore(config.data_file, config)
    await store.load()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    router = Router()

    async with aiohttp.ClientSession() as session:
        p2p = BinanceP2P(session, config)

        @router.message(CommandStart())
        async def start(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer(
                "Выбери стакан, который хочешь анализировать:",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer(
                "🟢 Зелёный — цена дошла до цели или ниже.\n"
                "🔴 Красный — цена дошла до цели или выше.",
                reply_markup=book_keyboard(user_book(user)),
            )

        @router.message(Command("settings"))
        async def settings_command(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer(
                user_settings_text(user),
                reply_markup=bottom_keyboard(user),
            )

        @router.message(F.text == BTN_SETTINGS)
        async def settings_button(message: Message) -> None:
            await settings_command(message)

        @router.message(F.text == BTN_BACK)
        async def back_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer(
                "Выбери стакан, который хочешь анализировать:",
                reply_markup=ReplyKeyboardRemove(),
            )
            await message.answer(
                "🟢 Зелёный — цена дошла до цели или ниже.\n"
                "🔴 Красный — цена дошла до цели или выше.",
                reply_markup=book_keyboard(user_book(user)),
            )

        @router.message(F.text == BTN_CHECK_GREEN)
        async def check_green_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer("Проверяю зелёный стакан...", reply_markup=bottom_keyboard(user))
            await send_current_price(bot, message.chat.id, p2p, user, BOOK_GREEN)

        @router.message(F.text == BTN_CHECK_RED)
        async def check_red_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer("Проверяю красный стакан...", reply_markup=bottom_keyboard(user))
            await send_current_price(bot, message.chat.id, p2p, user, BOOK_RED)

        @router.message(F.text == BTN_BOOK)
        async def book_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer(
                f"Сейчас выбран: <b>{book_title(user)}</b>.\n\n"
                "Красный стакан дополнительно скрывает объявления с ФОП/физ/IBAN и похожими условиями в описании.",
                reply_markup=book_keyboard(user_book(user), show_back=True),
            )

        @router.message(F.text == BTN_CHANGE_PRICE)
        async def change_price_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            await message.answer(
                "Выбери новую цель или введи свою цену:",
                reply_markup=bottom_keyboard(user),
            )
            await message.answer("Пресеты цены:", reply_markup=presets_keyboard(user_book(user)))

        @router.message(F.text == BTN_PAYMENTS)
        async def payments_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            selected = user_payment_methods(user)
            await message.answer(
                f"Фильтр оплаты: <b>{payment_title(selected)}</b>\n\n"
                "Можно выбрать один или несколько банков.",
                reply_markup=payments_keyboard(selected),
            )

        @router.message(F.text == BTN_AMOUNT)
        async def amount_button(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            amount_label = "Сумма сделки для красного стакана" if user_book(user) == BOOK_RED else "Минимальная сумма сделки"
            amount_prefix = "" if user_book(user) == BOOK_RED else "от "
            await message.answer(
                f"{amount_label}: <b>{amount_prefix}{user_min_trade_amount(user)} UAH</b>\n\n"
                "Выбери пресет или введи свою сумму.",
                reply_markup=amount_presets_keyboard(user_book(user)),
            )

        @router.callback_query(F.data == "settings")
        async def settings(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            await callback.message.edit_text(
                user_settings_text(user),
                reply_markup=main_keyboard(user),
            )
            await callback.answer()

        @router.callback_query(F.data.startswith("price_now:"))
        async def price_now(callback: CallbackQuery) -> None:
            book = callback.data.split(":", 1)[1]
            user = await store.ensure_user(callback.message.chat.id)
            if book not in BOOK_TITLES:
                await callback.answer("Неизвестный стакан", show_alert=True)
                return

            await callback.answer(f"Проверяю {book_title(book)}...")
            await send_current_price(bot, callback.message.chat.id, p2p, user, book)

        @router.callback_query(F.data == "book")
        async def book(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            await callback.message.edit_text(
                "Выбери стакан, который хочешь анализировать:\n\n"
                "🟢 Зелёный — цена дошла до цели или ниже.\n"
                "🔴 Красный — цена дошла до цели или выше.",
                reply_markup=book_keyboard(user_book(user)),
            )
            await callback.answer()

        @router.callback_query(F.data.startswith("book:set:"))
        async def book_set(callback: CallbackQuery) -> None:
            book = callback.data.split(":", 2)[2]
            if book not in BOOK_TITLES:
                await callback.answer("Неизвестный стакан", show_alert=True)
                return

            current_user = await store.ensure_user(callback.message.chat.id)
            current_book = user_book(current_user)
            target, min_trade_amount = default_values_for_book(book, config)
            target = parse_price(str(current_user.get(f"{book}_target", target)))
            min_trade_amount = parse_amount(str(current_user.get(f"{book}_min_trade_amount", min_trade_amount)))
            user = await store.update_user(
                callback.message.chat.id,
                **{
                    f"{current_book}_target": str(current_user["target"]),
                    f"{current_book}_min_trade_amount": str(user_min_trade_amount(current_user)),
                    f"{book}_target": str(target),
                    f"{book}_min_trade_amount": str(min_trade_amount),
                },
                book=book,
                target=str(target),
                min_trade_amount=str(min_trade_amount),
                awaiting_amount=False,
                awaiting_price=False,
                last_alert_adv_no=None,
            )
            await callback.message.edit_text(
                f"Готово. Теперь выбран <b>{book_title(user)}</b>.\n\n{user_settings_text(user)}",
                reply_markup=main_keyboard(user),
            )
            await callback.message.answer(
                "Меню выбранного стакана:",
                reply_markup=bottom_keyboard(user),
            )
            await callback.answer("Сохранено")

        @router.callback_query(F.data == "payments")
        async def payments(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            selected = user_payment_methods(user)
            await callback.message.edit_text(
                f"Фильтр оплаты: <b>{payment_title(selected)}</b>\n\n"
                "Можно выбрать один или несколько банков.",
                reply_markup=payments_keyboard(selected),
            )
            await callback.answer()

        @router.callback_query(F.data == "amount")
        async def amount(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            amount_label = "Сумма сделки для красного стакана" if user_book(user) == BOOK_RED else "Минимальная сумма сделки"
            amount_prefix = "" if user_book(user) == BOOK_RED else "от "
            await callback.message.edit_text(
                f"{amount_label}: <b>{amount_prefix}{user_min_trade_amount(user)} UAH</b>\n\n"
                "Выбери пресет или введи свою сумму.",
                reply_markup=amount_presets_keyboard(user_book(user)),
            )
            await callback.answer()

        @router.callback_query(F.data.startswith("amount_preset:"))
        async def amount_preset(callback: CallbackQuery) -> None:
            value = callback.data.split(":", 1)[1]
            current_user = await store.ensure_user(callback.message.chat.id)
            amount = parse_amount(value)
            user = await store.update_user(
                callback.message.chat.id,
                **{f"{user_book(current_user)}_min_trade_amount": str(amount)},
                min_trade_amount=str(amount),
                awaiting_amount=False,
                awaiting_price=False,
                last_alert_adv_no=None,
            )
            await callback.message.edit_text(
                f"Готово. Теперь сумма: <b>{'' if user_book(user) == BOOK_RED else 'от '}{user_min_trade_amount(user)} UAH</b>.",
                reply_markup=main_keyboard(user),
            )
            await callback.answer("Сохранено")

        @router.callback_query(F.data == "custom_amount")
        async def custom_amount(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            example = "15000" if user_book(user) == BOOK_RED else "2000"
            await store.update_user(
                callback.message.chat.id,
                awaiting_amount=True,
                awaiting_price=False,
            )
            await callback.message.edit_text(f"Напиши сумму в гривне, например <b>{example}</b>.")
            await callback.answer()

        @router.callback_query(F.data == "pay:all")
        async def pay_all(callback: CallbackQuery) -> None:
            user = await store.update_user(
                callback.message.chat.id,
                payment_methods=[],
                last_alert_adv_no=None,
            )
            await callback.message.edit_text(
                f"Фильтр оплаты: <b>{payment_title(user_payment_methods(user))}</b>\n\n"
                "Можно выбрать один или несколько банков.",
                reply_markup=payments_keyboard(user_payment_methods(user)),
            )
            await callback.answer("Выбраны все банки")

        @router.callback_query(F.data.startswith("pay:toggle:"))
        async def pay_toggle(callback: CallbackQuery) -> None:
            method = callback.data.split(":", 2)[2]
            if method not in PAYMENT_METHODS:
                await callback.answer("Неизвестный способ оплаты", show_alert=True)
                return

            user = await store.ensure_user(callback.message.chat.id)
            selected = user_payment_methods(user)
            if method in selected:
                selected.remove(method)
            else:
                selected.append(method)

            updated = await store.update_user(
                callback.message.chat.id,
                payment_methods=selected,
                last_alert_adv_no=None,
            )
            selected = user_payment_methods(updated)
            await callback.message.edit_text(
                f"Фильтр оплаты: <b>{payment_title(selected)}</b>\n\n"
                "Можно выбрать один или несколько банков.",
                reply_markup=payments_keyboard(selected),
            )
            await callback.answer()

        @router.callback_query(F.data == "change_price")
        async def change_price(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            await callback.message.edit_text(
                "Выбери новую цель или введи свою цену:",
                reply_markup=presets_keyboard(user_book(user)),
            )
            await callback.answer()

        @router.callback_query(F.data.startswith("preset:"))
        async def preset(callback: CallbackQuery) -> None:
            value = callback.data.split(":", 1)[1]
            current_user = await store.ensure_user(callback.message.chat.id)
            target = parse_price(value)
            user = await store.update_user(
                callback.message.chat.id,
                **{f"{user_book(current_user)}_target": str(target)},
                target=str(target),
                awaiting_price=False,
                awaiting_amount=False,
                last_alert_adv_no=None,
            )
            await callback.message.edit_text(
                f"Готово. Новая цель: <b>{user['target']} UAH</b> {user_price_direction(user)}.",
                reply_markup=main_keyboard(user),
            )
            await callback.answer("Сохранено")

        @router.callback_query(F.data == "custom_price")
        async def custom_price(callback: CallbackQuery) -> None:
            await store.update_user(
                callback.message.chat.id,
                awaiting_price=True,
                awaiting_amount=False,
            )
            await callback.message.edit_text("Напиши цену числом, например <b>44.85</b>.")
            await callback.answer()

        @router.message(F.text)
        async def text_price(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            if user.get("awaiting_amount"):
                try:
                    amount = parse_amount(message.text or "")
                except ValueError as exc:
                    await message.answer(str(exc))
                    return

                updated = await store.update_user(
                    message.chat.id,
                    **{f"{user_book(user)}_min_trade_amount": str(amount)},
                    min_trade_amount=str(amount),
                    awaiting_amount=False,
                    awaiting_price=False,
                    last_alert_adv_no=None,
                )
                await message.answer(
                    f"Готово. Теперь фильтр суммы: <b>{'' if user_book(updated) == BOOK_RED else 'от '}{user_min_trade_amount(updated)} UAH</b>.",
                    reply_markup=bottom_keyboard(updated),
                )
                return

            if not user.get("awaiting_price"):
                await message.answer("Выбери действие на клавиатуре ниже.", reply_markup=bottom_keyboard(user))
                return

            try:
                target = parse_price(message.text or "")
            except ValueError as exc:
                await message.answer(str(exc))
                return

            updated = await store.update_user(
                message.chat.id,
                **{f"{user_book(user)}_target": str(target)},
                target=str(target),
                awaiting_price=False,
                awaiting_amount=False,
                last_alert_adv_no=None,
            )
            await message.answer(
                f"Красиво. Теперь жду <b>{updated['target']} UAH</b> {user_price_direction(updated)}.",
                reply_markup=bottom_keyboard(updated),
            )

        dp.include_router(router)
        asyncio.create_task(watch_prices(bot, store, p2p, config.check_interval_seconds))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
