import os
import asyncio
import logging
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
    return web.Response(text="Bot is active and running!")

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
        "O'zbekiston va jahon tarixi fanidan toifa / attestatsiya imtihoniga tushadigan "
        "darajadagi bitta qiyin va qiziqarli test savolini tuzib ber. "
        "Javobni quyidagi JSON formatida qaytar:\n"
        "{\n"
        '  "question": "Savol matni",\n'
        '  "options": ["A variant", "B variant", "C variant", "D variant"],\n'
        '  "correct_option_id": 0,\n'
        '  "explanation": "Izoh"\n'
        "}\n"
        "Faqat JSON qaytar, boshqa ortiqcha matn yozma."
    )
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        import json
        clean_text = response.text.replace("`json", "").replace("```", "").strip()
        data = json.loads(clean_text)

        await bot.send_poll(
            chat_id=TARGET_CHAT_ID,
            question=data["question"],
            options=data["options"],
            type="quiz",
            correct_option_id=data["correct_option_id"],
            explanation=data.get("explanation", ""),
            is_anonymous=False
        )
        logging.info("Test muvaffaqiyatli yuborildi.")
    except Exception as e:
        logging.error(f"Xatolik: {e}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Tarix toifa test boti faol!")

@dp.message(Command("send_now"))
async def cmd_send_now(message: types.Message):
    await message.answer("Test yuborilmoqda...")
    await generate_and_send_test()
    await message.answer("Test kanalga yuborildi!")

async def schedule_loop():
    while True:
        await asyncio.sleep(3600)

async def main():
    await start_web_server()
    asyncio.create_task(schedule_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
