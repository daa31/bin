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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
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


@dataclass
class Config:
    bot_token: str
    default_target_price: Decimal
    check_interval_seconds: int
    asset: str
    fiat: str
    trade_type: str
    data_file: Path


class UserStore:
    def __init__(self, path: Path, default_target: Decimal) -> None:
        self.path = path
        self.default_target = default_target
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
            "target": str(self.default_target),
            "enabled": True,
            "awaiting_price": False,
            "payment_methods": [],
            "last_alert_adv_no": None,
        }

    async def ensure_user(self, user_id: int) -> dict[str, Any]:
        async with self._lock:
            key = str(user_id)
            if key not in self._data:
                self._data[key] = self._default_user()
                await self._save_locked()
            return dict(self._data[key])

    async def update_user(self, user_id: int, **values: Any) -> dict[str, Any]:
        async with self._lock:
            key = str(user_id)
            user = self._data.setdefault(key, self._default_user())
            user.update(values)
            await self._save_locked()
            return dict(user)

    async def all_users(self) -> dict[int, dict[str, Any]]:
        async with self._lock:
            return {int(user_id): dict(data) for user_id, data in self._data.items()}


class BinanceP2P:
    def __init__(self, session: aiohttp.ClientSession, config: Config) -> None:
        self.session = session
        self.config = config

    async def best_offer(self, pay_types: list[str] | None = None) -> dict[str, Any] | None:
        pay_types = pay_types or []
        payload = {
            "fiat": self.config.fiat,
            "page": 1,
            "rows": 10,
            "tradeType": self.config.trade_type,
            "asset": self.config.asset,
            "payTypes": pay_types,
            "publisherType": None,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 Binance price alert bot",
        }
        async with self.session.post(BINANCE_P2P_URL, json=payload, headers=headers, timeout=20) as response:
            response.raise_for_status()
            body = await response.json()

        rows = body.get("data") or []
        if not rows:
            return None

        row = rows[0]
        adv = row["adv"]
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
            "link": binance_web_url(self.config, pay_types),
        }


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set BOT_TOKEN environment variable.")

    return Config(
        bot_token=token,
        default_target_price=parse_price(os.getenv("DEFAULT_TARGET_PRICE", "44.9")),
        check_interval_seconds=max(15, int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))),
        asset=os.getenv("BINANCE_ASSET", "USDT").upper(),
        fiat=os.getenv("BINANCE_FIAT", "UAH").upper(),
        trade_type=os.getenv("BINANCE_TRADE_TYPE", "BUY").upper(),
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


def binance_web_url(config: Config, pay_types: list[str] | None = None) -> str:
    base = (
        f"https://p2p.binance.com/ru/trade/"
        f"{config.trade_type.lower()}/{config.asset}?fiat={config.fiat}"
    )
    if not pay_types:
        return base
    return f"{base}&payment={','.join(pay_types)}"


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
    enabled_text = "Пауза" if user.get("enabled", True) else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Цена сейчас", callback_data="price_now"),
                InlineKeyboardButton(text="Моя цель", callback_data="settings"),
            ],
            [
                InlineKeyboardButton(text="Изменить цену", callback_data="change_price"),
                InlineKeyboardButton(text="Банк", callback_data="payments"),
            ],
            [
                InlineKeyboardButton(text=enabled_text, callback_data="toggle"),
            ],
        ]
    )


def presets_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="44.50", callback_data="preset:44.50"),
                InlineKeyboardButton(text="44.90", callback_data="preset:44.90"),
                InlineKeyboardButton(text="45.20", callback_data="preset:45.20"),
            ],
            [InlineKeyboardButton(text="Ввести свою", callback_data="custom_price")],
            [InlineKeyboardButton(text="Назад", callback_data="settings")],
        ]
    )


def payments_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Все банки" if not selected else "Все банки", callback_data="pay:all")]]
    methods = list(PAYMENT_METHODS.items())
    for index in range(0, len(methods), 2):
        row = []
        for code, title in methods[index : index + 2]:
            prefix = "✅ " if code in selected else ""
            row.append(InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"pay:toggle:{code}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Готово", callback_data="settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def offer_text(offer: dict[str, Any], target: Decimal | None = None) -> str:
    target_line = f"\nТвоя цель: <b>{target} {offer['fiat']}</b>" if target is not None else ""
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

    return (
        f"🟢 <b>{offer['asset']}/{offer['fiat']}: {offer['price']} {offer['fiat']}</b>"
        f"{target_line}\n\n"
        f"Продавец: <b>{html.escape(str(offer['merchant']))}</b>\n"
        f"Лимиты: {offer.get('min_amount') or '-'} - {offer.get('max_amount') or '-'} {offer['fiat']}\n"
        f"Доступно: {offer.get('available') or '-'} {offer['asset']}\n"
        f"Оплата: {html.escape(methods_line)}\n"
        f"Сделок за месяц: {orders}, завершение: {finish_rate}\n\n"
        f"<a href=\"{offer['link']}\">Открыть объявление на Binance P2P</a>\n"
        f"ID объявления: <code>{offer['adv_no']}</code>"
    )


def alert_text(offer: dict[str, Any], target: Decimal) -> str:
    return "🔥 <b>Цена дошла до цели</b>\n\n" + offer_text(offer, target)


async def send_price(
    bot: Bot,
    chat_id: int,
    p2p: BinanceP2P,
    target: Decimal | None = None,
    pay_types: list[str] | None = None,
) -> None:
    offer = await p2p.best_offer(pay_types)
    if offer is None:
        await bot.send_message(chat_id, "Сейчас Binance не вернул объявления под этот фильтр. Попробуй другой банк или чуть позже.")
        return
    await bot.send_message(
        chat_id,
        offer_text(offer, target),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть Binance", url=offer["link"])]]
        ),
        disable_web_page_preview=True,
    )


async def watch_prices(bot: Bot, store: UserStore, p2p: BinanceP2P, interval: int) -> None:
    while True:
        try:
            users = await store.all_users()
            offers_by_filter: dict[tuple[str, ...], dict[str, Any] | None] = {}
            for chat_id, user in users.items():
                if not user.get("enabled", True):
                    continue
                pay_types = user_payment_methods(user)
                filter_key = tuple(pay_types)
                if filter_key not in offers_by_filter:
                    offers_by_filter[filter_key] = await p2p.best_offer(pay_types)
                offer = offers_by_filter[filter_key]
                if offer is not None:
                    target = parse_price(str(user.get("target", p2p.config.default_target_price)))
                    adv_no = offer["adv_no"]
                    price = offer["price"]
                    if price <= target and user.get("last_alert_adv_no") != adv_no:
                        await bot.send_message(
                            chat_id,
                            alert_text(offer, target),
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[[InlineKeyboardButton(text="Открыть Binance", url=offer["link"])]]
                            ),
                            disable_web_page_preview=True,
                        )
                        await store.update_user(chat_id, last_alert_adv_no=adv_no)
                    elif price > target and user.get("last_alert_adv_no") is not None:
                        await store.update_user(chat_id, last_alert_adv_no=None)
        except Exception:
            logging.exception("Price watcher failed")

        await asyncio.sleep(interval)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    store = UserStore(config.data_file, config.default_target_price)
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
                "Я слежу за зелёным стаканом Binance P2P USDT/UAH.\n"
                f"По умолчанию цель: <b>{user['target']} UAH</b>. "
                f"Банк: <b>{payment_title(user_payment_methods(user))}</b>.\n"
                "Как только цена будет такой или ниже, пришлю объявление.",
                reply_markup=main_keyboard(user),
            )

        @router.message(Command("settings"))
        async def settings_command(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            status = "включены" if user.get("enabled", True) else "на паузе"
            await message.answer(
                f"Цель: <b>{user['target']} UAH</b>\n"
                f"Банк: <b>{payment_title(user_payment_methods(user))}</b>\n"
                f"Оповещения: <b>{status}</b>",
                reply_markup=main_keyboard(user),
            )

        @router.callback_query(F.data == "settings")
        async def settings(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            status = "включены" if user.get("enabled", True) else "на паузе"
            await callback.message.edit_text(
                f"Цель: <b>{user['target']} UAH</b>\n"
                f"Банк: <b>{payment_title(user_payment_methods(user))}</b>\n"
                f"Оповещения: <b>{status}</b>",
                reply_markup=main_keyboard(user),
            )
            await callback.answer()

        @router.callback_query(F.data == "price_now")
        async def price_now(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            await callback.answer("Проверяю Binance...")
            await send_price(
                bot,
                callback.message.chat.id,
                p2p,
                parse_price(str(user["target"])),
                user_payment_methods(user),
            )

        @router.callback_query(F.data == "toggle")
        async def toggle(callback: CallbackQuery) -> None:
            user = await store.ensure_user(callback.message.chat.id)
            updated = await store.update_user(callback.message.chat.id, enabled=not user.get("enabled", True))
            status = "включены" if updated.get("enabled", True) else "на паузе"
            await callback.message.edit_text(
                f"Цель: <b>{updated['target']} UAH</b>\n"
                f"Банк: <b>{payment_title(user_payment_methods(updated))}</b>\n"
                f"Оповещения: <b>{status}</b>",
                reply_markup=main_keyboard(updated),
            )
            await callback.answer()

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
            await callback.message.edit_text("Выбери новую цель или введи свою цену:", reply_markup=presets_keyboard())
            await callback.answer()

        @router.callback_query(F.data.startswith("preset:"))
        async def preset(callback: CallbackQuery) -> None:
            value = callback.data.split(":", 1)[1]
            user = await store.update_user(
                callback.message.chat.id,
                target=str(parse_price(value)),
                awaiting_price=False,
                last_alert_adv_no=None,
            )
            await callback.message.edit_text(
                f"Готово. Новая цель: <b>{user['target']} UAH</b>",
                reply_markup=main_keyboard(user),
            )
            await callback.answer("Сохранено")

        @router.callback_query(F.data == "custom_price")
        async def custom_price(callback: CallbackQuery) -> None:
            await store.update_user(callback.message.chat.id, awaiting_price=True)
            await callback.message.edit_text("Напиши цену числом, например <b>44.85</b>.")
            await callback.answer()

        @router.message(F.text)
        async def text_price(message: Message) -> None:
            user = await store.ensure_user(message.chat.id)
            if not user.get("awaiting_price"):
                await message.answer("Открой настройки кнопкой ниже.", reply_markup=main_keyboard(user))
                return

            try:
                target = parse_price(message.text or "")
            except ValueError as exc:
                await message.answer(str(exc))
                return

            updated = await store.update_user(
                message.chat.id,
                target=str(target),
                awaiting_price=False,
                last_alert_adv_no=None,
            )
            await message.answer(
                f"Красиво. Теперь жду <b>{updated['target']} UAH</b> или ниже.",
                reply_markup=main_keyboard(updated),
            )

        dp.include_router(router)
        asyncio.create_task(watch_prices(bot, store, p2p, config.check_interval_seconds))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
