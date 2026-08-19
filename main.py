import os
import asyncio
import logging
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web
from google import genai

logging.basicConfig(level=logging.INFO)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/healthz", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Veb server {port}-portda ishga tushdi.")

async def generate_and_send_test():
    prompt = (
        "O'zbekiston va jahon tarixi fanidan toifa imtihoni uchun 1 ta qiyin test savolini tuzib ber.\n"
        "Qoidalar:\n"
        "1. Savol 250 belgidan oshmasin.\n"
        "2. Har bir variant 80 belgidan oshmasin.\n"
        "3. Izoh 150 belgidan oshmasin.\n"
        "Faqat quyidagi JSON formatida qaytar:\n"
        "{\n"
        '  "question": "Savol matni",\n'
        '  "options": ["A", "B", "C", "D"],\n'
        '  "correct_option_id": 0,\n'
        '  "explanation": "Izoh"\n'
        "}"
    )
    try:
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
        )
        text = response.text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != 0:
            text = text[start:end]

        data = json.loads(text)

        question = str(data.get("question", "Tarix testi"))[:255]
        options = [str(opt)[:99] for opt in data.get("options", [])[:10]]
        correct_id = int(data.get("correct_option_id", 0))
        if correct_id >= len(options) or correct_id < 0:
            correct_id = 0
        explanation = str(data.get("explanation", ""))[:199]

        await bot.send_poll(
            chat_id=TARGET_CHAT_ID,
            question=question,
            options=options,
            type="quiz",
            correct_option_id=correct_id,
            explanation=explanation if len(explanation) > 0 else None,
            is_anonymous=True
        )
        logging.info("Test kanalga yuborildi!")
    except Exception as e:
        logging.error(f"Xatolik: {e}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Tarix toifa test boti faol!")

@dp.message(Command("send_now"))
async def cmd_send_now(message: types.Message):
    await message.answer("Test yuborilmoqda...")
    await generate_and_send_test()
    await message.answer("Jarayon yakunlandi!")

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
