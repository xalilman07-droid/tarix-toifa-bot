import os
import asyncio
import logging
import json

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web
from google import genai


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")


if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable topilmadi!")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable topilmadi!")

if not TARGET_CHAT_ID:
    raise RuntimeError("TARGET_CHAT_ID environment variable topilmadi!")


try:
    TARGET_CHAT_ID = int(TARGET_CHAT_ID)
except ValueError:
    raise RuntimeError(
        "TARGET_CHAT_ID raqam bo'lishi kerak. "
        "Masalan: -1001234567890"
    )


# =========================================================
# BOT / DISPATCHER / GEMINI
# =========================================================

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

ai_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# WEB SERVER
# Render uchun kerak
# =========================================================

async def handle_ping(request):
    return web.Response(
        text="Tarix test bot ishlayapti!"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", handle_ping)
    app.router.add_get("/healthz", handle_ping)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    logger.info(
        f"Veb server {port}-portda ishga tushdi."
    )


# =========================================================
# GEMINI JSON TOZALASH
# =========================================================

def extract_json(text: str):
    """
    Gemini javobidan JSON qismini ajratib oladi.
    """

    text = text.strip()

    # ```json ... ``` bo'lsa olib tashlash
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # JSON obyekt chegaralarini topish
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(
            "Gemini javobidan JSON topilmadi."
        )

    json_text = text[start:end + 1]

    return json.loads(json_text)


# =========================================================
# TEST GENERATSIYA QILISH VA KANALGA YUBORISH
# =========================================================

async def generate_and_send_test():

    prompt = """
O'zbekiston va jahon tarixi fanidan toifa imtihoni uchun
1 ta qiyin, aniq va tarixiy faktlarga asoslangan test savolini tuz.

Talablar:

1. Savol 250 belgidan oshmasin.
2. Aynan 4 ta javob varianti bo'lsin.
3. Har bir variant 100 belgidan oshmasin.
4. Faqat BITTA variant to'g'ri bo'lsin.
5. correct_option_id 0, 1, 2 yoki 3 bo'lsin.
6. Izoh 200 belgidan oshmasin.
7. Savol va variantlar o'zbek tilida bo'lsin.
8. Savol imtihon darajasida qiyin bo'lsin.
9. Taxminiy yoki uydirma tarixiy ma'lumot ishlatma.

Faqat quyidagi JSON formatida javob ber:

{
  "question": "Savol matni",
  "options": [
    "A variant",
    "B variant",
    "C variant",
    "D variant"
  ],
  "correct_option_id": 0,
  "explanation": "Qisqa tushuntirish"
}
"""

    try:

        logger.info("Gemini'dan test so'ralmoqda...")

        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        text = response.text

        if not text:
            raise ValueError(
                "Gemini bo'sh javob qaytardi."
            )

        logger.info(
            "Gemini javobi olindi."
        )

        data = extract_json(text)

        # -------------------------------------------------
        # QUESTION
        # -------------------------------------------------

        question = str(
            data.get(
                "question",
                "Tarix testi"
            )
        ).strip()

        question = question[:255]

        # -------------------------------------------------
        # OPTIONS
        # -------------------------------------------------

        raw_options = data.get(
            "options",
            []
        )

        if not isinstance(raw_options, list):
            raise ValueError(
                "options list formatida emas."
            )

        options = [
            str(option).strip()[:100]
            for option in raw_options
        ]

        # Telegram poll uchun 2-10 variant
        if len(options) < 2:
            raise ValueError(
                "Kamida 2 ta variant kerak."
            )

        if len(options) > 10:
            options = options[:10]

        # -------------------------------------------------
        # CORRECT ANSWER
        # -------------------------------------------------

        try:
            correct_id = int(
                data.get(
                    "correct_option_id",
                    0
                )
            )
        except (ValueError, TypeError):
            correct_id = 0

        if correct_id < 0 or correct_id >= len(options):
            correct_id = 0

        # -------------------------------------------------
        # EXPLANATION
        # -------------------------------------------------

        explanation = str(
            data.get(
                "explanation",
                ""
            )
        ).strip()

        explanation = explanation[:200]

        # -------------------------------------------------
        # TELEGRAM POLL
        # -------------------------------------------------

        logger.info(
            "Telegram kanaliga quiz yuborilmoqda..."
        )

        await bot.send_poll(
            chat_id=TARGET_CHAT_ID,
            question=question,
            options=options,
            type="quiz",
            correct_option_id=correct_id,
            explanation=(
                explanation
                if explanation
                else None
            ),
            is_anonymous=True
        )

        logger.info(
            "✅ Test kanalga muvaffaqiyatli yuborildi!"
        )

        return True

    except Exception as e:

        logger.exception(
            f"Test yuborishda xatolik: {e}"
        )

        return False


# =========================================================
# /START
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):

    await message.answer(
        "📚 Tarix Toifa Test bot faol!\n\n"
        "Test yuborish uchun:\n"
        "/send_now"
    )


# =========================================================
# /SEND_NOW
# =========================================================

@dp.message(Command("send_now"))
async def cmd_send_now(message: types.Message):

    await message.answer(
        "⏳ Test tayyorlanmoqda..."
    )

    success = await generate_and_send_test()

    if success:

        await message.answer(
            "✅ Test kanalga muvaffaqiyatli yuborildi!"
        )

    else:

        await message.answer(
            "❌ Test yuborishda xatolik yuz berdi. "
            "Render loglarini tekshiring."
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    await start_web_server()

    logger.info(
        "Telegram bot polling boshlanmoqda..."
    )

    await dp.start_polling(bot)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
