import os
import asyncio
import json
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web
from google import genai


# =========================
# SOZLAMALAR
# =========================

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi!")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi!")

if not TARGET_CHAT_ID:
    raise RuntimeError("TARGET_CHAT_ID topilmadi!")


# =========================
# BOT VA GEMINI
# =========================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ai_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================
# RENDER WEB SERVER
# =========================

async def home(request):
    return web.Response(
        text="Tarix test bot ishlayapti!"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", home)
    app.router.add_get("/healthz", home)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    logging.info(
        f"Web server {port}-portda ishga tushdi."
    )


# =========================
# GEMINI'DAN JSON OLISH
# =========================

def clean_json(text):
    text = text.strip()

    # ```json ... ``` formatini tozalash
    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # JSON chegaralarini topish
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(
            "Gemini javobidan JSON topilmadi!"
        )

    return json.loads(
        text[start:end + 1]
    )


# =========================
# TEST YARATISH
# =========================

async def generate_and_send_test():

    prompt = """
O'zbekiston va jahon tarixi fanidan
toifa imtihoni darajasida 1 ta qiyin test tuz.

Talablar:

- Savol o'zbek tilida bo'lsin.
- Savol 250 belgidan oshmasin.
- Aynan 4 ta variant bo'lsin.
- Har bir variant 100 belgidan oshmasin.
- Faqat 1 ta to'g'ri javob bo'lsin.
- correct_option_id 0, 1, 2 yoki 3 bo'lsin.
- Izoh 200 belgidan oshmasin.
- Tarixiy faktlar aniq bo'lsin.
- Uydirma ma'lumot yozma.

Faqat JSON qaytar:

{
  "question": "Savol",
  "options": [
    "A variant",
    "B variant",
    "C variant",
    "D variant"
  ],
  "correct_option_id": 0,
  "explanation": "Qisqa izoh"
}
"""

    try:

        logging.info(
            "Gemini'dan test olinmoqda..."
        )

        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        if not response.text:
            raise ValueError(
                "Gemini bo'sh javob qaytardi!"
            )

        data = clean_json(
            response.text
        )

        # Savol
        question = str(
            data.get(
                "question",
                "Tarix testi"
            )
        ).strip()

        question = question[:255]

        # Variantlar
        options = data.get(
            "options",
            []
        )

        if not isinstance(options, list):
            raise ValueError(
                "Variantlar noto'g'ri formatda!"
            )

        options = [
            str(x).strip()[:100]
            for x in options
        ]

        if len(options) != 4:
            raise ValueError(
                f"Testda 4 ta variant bo'lishi kerak. "
                f"Hozir: {len(options)} ta"
            )

        # To'g'ri javob
        try:
            correct_id = int(
                data.get(
                    "correct_option_id",
                    0
                )
            )
        except:
            correct_id = 0

        if correct_id not in [0, 1, 2, 3]:
            correct_id = 0

        # Izoh
        explanation = str(
            data.get(
                "explanation",
                ""
            )
        ).strip()

        explanation = explanation[:200]

        # Telegram kanaliga yuborish
        logging.info(
            f"Test {TARGET_CHAT_ID} kanaliga yuborilmoqda..."
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

        logging.info(
            "TEST MUVAFFAQIYATLI YUBORILDI!"
        )

        return True

    except Exception as e:

        logging.exception(
            f"TEST YUBORISHDA XATO: {e}"
        )

        return False


# =========================
# /START
# =========================

@dp.message(Command("start"))
async def start_command(
    message: types.Message
):

    await message.answer(
        "📚 Tarix Toifa Test bot ishlayapti!\n\n"
        "Yangi test yuborish uchun:\n"
        "/send_now"
    )


# =========================
# /SEND_NOW
# =========================

@dp.message(Command("send_now"))
async def send_now_command(
    message: types.Message
):

    await message.answer(
        "⏳ Test tayyorlanmoqda..."
    )

    success = await generate_and_send_test()

    if success:

        await message.answer(
            "✅ Test kanalga yuborildi!"
        )

    else:

        await message.answer(
            "❌ Test yuborilmadi.\n"
            "Render loglarini tekshiring."
        )


# =========================
# MAIN
# =========================

async def main():

    await start_web_server()

    logging.info(
        "Telegram bot ishga tushmoqda..."
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())




