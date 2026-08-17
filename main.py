import os
import json
import logging
import asyncio
from datetime import datetime
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from google import genai

# ================= SOZLAMALAR =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_API_KEY)

TASHKENT_TZ = pytz.timezone("Asia/Tashkent")


# ================= GEMINI GENERATSIYA =================
def generate_batch_tests(batch_num: int, count: int = 10):
    prompt = f"""
    O'zbekiston tarixi va Jahon tarixidan Kasbiy Toifa (attestatsiya) imtihonlari darajasidagi {count} ta murakkab va yangi test savolini tuzing.
    
    Qat'iy talab: Faqat quyidagi JSON formatida toza massiv qaytaring:
    [
      {{
        "question": "Savol matni...",
        "options": ["Variant A", "Variant B", "Variant C", "Variant D"],
        "correct_index": 0,
        "explanation": "To'g'ri javobning qisqa ilmiy izohi"
      }}
    ]
    """
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        return json.loads(response.text)
    except Exception as e:
        logging.error(f"Partiya {batch_num} da xatolik: {e}")
        return []


# ================= TESTLARNI KANALGA YUBORISH =================
async def send_daily_quiz_pack():
    logging.info("Kunlik 50 ta testni yuklash boshlandi...")
    
    try:
        await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="🌅 **Assalomu alaykum, hurmatli ustozlar!**\n\n"
                 "Bugungi kunlik 50 ta Kasbiy Toifa (attestatsiya) testlari boshlanmoqda.\n"
                 "O'z bilimingizni sinab ko'ring! 👇",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Kanalga yozishda xatolik: {e}")

    total_sent = 0

    for batch_num in range(1, 6):
        tests = generate_batch_tests(batch_num=batch_num, count=10)
        
        for q in tests:
            total_sent += 1
            try:
                question_text = f"[{total_sent}/50] {q['question']}"[:300]
                options = [str(opt)[:100] for opt in q['options']]
                explanation = str(q.get('explanation', ''))[:200]
                
                await bot.send_poll(
                    chat_id=TARGET_CHAT_ID,
                    question=question_text,
                    options=options,
                    type='quiz',
                    correct_option_id=int(q['correct_index']),
                    explanation=explanation if explanation else None,
                    is_anonymous=False
                )
                await asyncio.sleep(1.5)
            except Exception as err:
                logging.error(f"Test yuborishda xatolik: {err}")
        
        await asyncio.sleep(2)

    logging.info(f"Jami {total_sent} ta test joylandi!")


# ================= VAQTNI BOSHQARISH (06:00 TASHKENT) =================
async def scheduler_task():
    sent_today = False
    while True:
        now_tashkent = datetime.now(TASHKENT_TZ)
        
        if now_tashkent.hour == 6 and now_tashkent.minute == 0 and not sent_today:
            await send_daily_quiz_pack()
            sent_today = True
        
        if now_tashkent.hour == 6 and now_tashkent.minute > 0:
            sent_today = False
            
        await asyncio.sleep(20)


# ================= ADMIN BUYRUQLARI =================
@dp.message(Command("send_now"))
async def cmd_send_now(message: types.Message):
    await message.answer("50 ta test generatsiya qilinib, kanalga jo'natilmoqda...")
    await send_daily_quiz_pack()
    await message.answer("Testlar kanalga to'liq joylandi!")


# ================= ASOSIY MAIN =================
async def main():
    asyncio.create_task(scheduler_task())
    logging.info("Bot serverda muvaffaqiyatli ishga tushdi.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
