import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from google import genai
from google.genai import types as genai_types


# ============================================================
# SOZLAMALAR
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

# Har kuni qaysi vaqtda boshlansin
SEND_HOUR = int(os.getenv("SEND_HOUR", "8"))
SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))

# Bir kunda nechta test
DAILY_TEST_COUNT = 50

# Bir Gemini so'rovida nechta test
BATCH_SIZE = 10

# Toshkent vaqti
UZ_TZ = ZoneInfo("Asia/Tashkent")


# ============================================================
# ENVIRONMENT TEKSHIRISH
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN topilmadi!"
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY topilmadi!"
    )

if not TARGET_CHAT_ID:
    raise RuntimeError(
        "TARGET_CHAT_ID topilmadi!"
    )


# ============================================================
# BOT / GEMINI
# ============================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# STATISTIKA
# ============================================================

last_run_date = None
last_run_time = None
last_error = None
today_sent = 0


# ============================================================
# RENDER WEB SERVER
# ============================================================

async def home(request):
    return web.Response(
        text="Tarix Toifa Test Bot ishlayapti!"
    )


async def health(request):
    return web.Response(
        text="OK"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get("/", home)
    app.router.add_get("/healthz", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(
        os.getenv("PORT", "10000")
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    logger.info(
        f"Web server {port}-portda ishga tushdi."
    )


# ============================================================
# GEMINI JSON SCHEMA
# ============================================================

TEST_SCHEMA = {
    "type": "object",
    "properties": {
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string"
                    },
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "correct_option_id": {
                        "type": "integer"
                    },
                    "explanation": {
                        "type": "string"
                    }
                },
                "required": [
                    "question",
                    "options",
                    "correct_option_id",
                    "explanation"
                ]
            }
        }
    },
    "required": [
        "tests"
    ]
}


# ============================================================
# GEMINI PROMPT
# ============================================================

def build_prompt(batch_number):

    if batch_number % 2 == 1:
        topic_instruction = """
Bu blokda O'ZBEKISTON TARIXI ustun bo'lsin.
Qadimgi davr, o'rta asrlar, xonliklar,
Rossiya imperiyasi davri, jadidchilik,
sovet davri va mustaqillik davrini aralashtir.
"""
    else:
        topic_instruction = """
Bu blokda JAHON TARIXI ustun bo'lsin.
Qadimgi Sharq, Yunoniston, Rim,
o'rta asrlar, Uyg'onish, Buyuk geografik kashfiyotlar,
sanoat inqilobi, jahon urushlari va XX asr tarixini aralashtir.
"""

    return f"""
SEN — tarix fanidan O'zbekiston Respublikasidagi
TOIFA IMTIHONI uchun professional test tuzuvchi ekspertisan.

Bugungi kun uchun 10 ta yangi test savoli yarat.

Bu {batch_number}-blok.

{topic_instruction}

MUHIM TALABLAR:

1. Har bir savol professional toifa imtihoni darajasida bo'lsin.
2. Savollar oddiy "kim?", "qachon?" yodlash savollaridan
   ko'ra tahliliy va sabab-oqibatli bo'lishiga ustuvorlik ber.
3. Xronologiya, moslashtirish, tarixiy sabab-oqibat,
   davlatlar siyosati, islohotlar, tarixiy shaxslar,
   hujjatlar va muhim voqealardan foydalan.
4. Savollar bir-biriga o'xshamasin.
5. Bir xil savol yoki bir xil javob kombinatsiyasini takrorlama.
6. O'zbekiston tarixi va jahon tarixini muvozanatli qamrab ol.
7. Tarixiy faktlarni uydirma qilma.
8. Sana va ism-shariflarni imkon qadar aniq yoz.
9. Har bir savolda A, B, C, D — aynan 4 ta variant bo'lsin.
10. Faqat BITTA to'g'ri javob bo'lsin.
11. correct_option_id:
    A = 0
    B = 1
    C = 2
    D = 3
12. Savol 300 belgidan oshmasin.
13. Har bir variant 100 belgidan oshmasin.
14. Izoh 200 belgidan oshmasin.
15. Noto'g'ri variantlar mantiqan ishonarli bo'lsin.
16. To'g'ri javobni boshqa variantlardan grammatik yoki
    uzunlik jihatdan juda oson ajratib bo'lmasin.
17. Testlar o'zbek adabiy tilida bo'lsin.
18. Savollarni bugungi boshqa bloklardagi savollardan farqli qil.
19. 10 ta testning o'zida ham mavzularni takrorlama.

Faqat JSON qaytar.

JSON quyidagi shaklda bo'lishi shart:

{{
  "tests": [
    {{
      "question": "Savol",
      "options": [
        "A variant",
        "B variant",
        "C variant",
        "D variant"
      ],
      "correct_option_id": 0,
      "explanation": "Qisqa tarixiy izoh"
    }}
  ]
}}

Aynan 10 ta test yarat.
"""


# ============================================================
# GEMINI'DAN 10 TA TEST OLISH
# ============================================================

async def generate_batch(batch_number):

    prompt = build_prompt(
        batch_number
    )

    logger.info(
        f"Gemini: {batch_number}-blok "
        f"tayyorlanmoqda..."
    )

    response = await asyncio.to_thread(
        gemini.models.generate_content,
        model="gemini-3.6-flash",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.65,
            max_output_tokens=12000,
            response_mime_type="application/json",
            response_schema=TEST_SCHEMA
        )
    )

    if not response.text:
        raise ValueError(
            "Gemini bo'sh javob qaytardi!"
        )

    data = json.loads(
        response.text
    )

    tests = data.get(
        "tests",
        []
    )

    if not isinstance(tests, list):
        raise ValueError(
            "Gemini 'tests' massivini qaytarmadi!"
        )

    valid_tests = []

    for item in tests:

        try:

            question = str(
                item["question"]
            ).strip()

            options = item["options"]

            correct_id = int(
                item["correct_option_id"]
            )

            explanation = str(
                item.get(
                    "explanation",
                    ""
                )
            ).strip()

            if not isinstance(
                options,
                list
            ):
                continue

            options = [
                str(x).strip()
                for x in options
            ]

            # Qattiq tekshiruv
            if len(question) < 10:
                continue

            if len(question) > 300:
                continue

            if len(options) != 4:
                continue

            if any(
                len(x) < 1 or len(x) > 100
                for x in options
            ):
                continue

            if correct_id not in [0, 1, 2, 3]:
                continue

            explanation = explanation[:200]

            valid_tests.append({
                "question": question,
                "options": options,
                "correct_option_id": correct_id,
                "explanation": explanation
            })

        except Exception:
            continue

    logger.info(
        f"{batch_number}-blokdan "
        f"{len(valid_tests)} ta sifatli test olindi."
    )

    return valid_tests


# ============================================================
# TESTLARNI TAKRORLANISHDAN TOZALASH
# ============================================================

def remove_duplicates(tests):

    unique = []
    seen = set()

    for test in tests:

        key = (
            test["question"]
            .lower()
            .replace(" ", "")
            .replace(".", "")
            .replace(",", "")
            .replace("?", "")
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(test)

    return unique


# ============================================================
# 50 TA TEST TAYYORLASH
# ============================================================

async def generate_50_tests():

    all_tests = []

    # 5 × 10 = 50
    for batch in range(1, 6):

        for attempt in range(1, 4):

            try:

                tests = await generate_batch(
                    batch
                )

                all_tests.extend(
                    tests
                )

                break

            except Exception as e:

                logger.error(
                    f"{batch}-blok, "
                    f"{attempt}-urinish xato: {e}"
                )

                if attempt < 3:
                    await asyncio.sleep(5)

    all_tests = remove_duplicates(
        all_tests
    )

    logger.info(
        f"Jami noyob testlar: "
        f"{len(all_tests)}"
    )

    # Yetarli bo'lmasa qayta generatsiya
    if len(all_tests) < 50:

        logger.warning(
            "50 ta test yig'ilmadi. "
            "Qo'shimcha testlar yaratilmoqda..."
        )

        extra = await generate_batch(
            6
        )

        all_tests.extend(
            extra
        )

        all_tests = remove_duplicates(
            all_tests
        )

    if len(all_tests) < 50:

        raise RuntimeError(
            f"50 ta test yaratib bo'lmadi. "
            f"Faqat {len(all_tests)} ta mavjud."
        )

    return all_tests[:50]


# ============================================================
# BITTA TESTNI TELEGRAMGA YUBORISH
# ============================================================

async def send_one_test(test, number):

    await bot.send_poll(
        chat_id=TARGET_CHAT_ID,
        question=test["question"],
        options=test["options"],
        type="quiz",
        correct_option_id=test[
            "correct_option_id"
        ],
        explanation=(
            test["explanation"]
            if test["explanation"]
            else None
        ),
        is_anonymous=True
    )

    logger.info(
        f"✅ {number}/50 test yuborildi."
    )


# ============================================================
# 50 TA TESTNI KANALGA YUBORISH
# ============================================================

async def send_daily_tests():

    global last_run_date
    global last_run_time
    global last_error
    global today_sent

    now = datetime.now(
        UZ_TZ
    )

    today = now.date()

    # Shu kun allaqachon yuborilgan bo'lsa,
    # ikkinchi marta yubormaydi.
    if last_run_date == today:

        logger.warning(
            "Bugungi 50 ta test allaqachon yuborilgan."
        )

        return

    today_sent = 0

    try:

        logger.info(
            "========================================"
        )

        logger.info(
            "🚀 BUGUNGI 50 TA TEST TAYYORLANMOQDA"
        )

        logger.info(
            f"📅 Sana: {today}"
        )

        logger.info(
            "========================================"
        )

        tests = await generate_50_tests()

        logger.info(
            "50 ta test tayyor."
        )

        for index, test in enumerate(
            tests,
            start=1
        ):

            try:

                await send_one_test(
                    test,
                    index
                )

                today_sent += 1

                # Telegram serverini ortiqcha
                # bosmaslik uchun kichik pauza
                await asyncio.sleep(1.2)

            except Exception as e:

                logger.error(
                    f"{index}/50 yuborilmadi: {e}"
                )

                # Bitta test xatosi qolgan 49 tasini
                # to'xtatmasin.
                await asyncio.sleep(3)

        last_run_date = today
        last_run_time = datetime.now(
            UZ_TZ
        )

        last_error = None

        logger.info(
            f"🎉 BUGUNGI YUBORISH YAKUNLANDI: "
            f"{today_sent}/50"
        )

    except Exception as e:

        last_error = str(e)

        logger.exception(
            f"❌ Kundalik testlar xatosi: {e}"
        )


# ============================================================
# KEYINGI 08:00 NI TOPISH
# ============================================================

def next_run_time():

    now = datetime.now(
        UZ_TZ
    )

    target = now.replace(
        hour=SEND_HOUR,
        minute=SEND_MINUTE,
        second=0,
        microsecond=0
    )

    if now >= target:
        target += timedelta(
            days=1
        )

    return target


# ============================================================
# AVTOMATIK SCHEDULER
# ============================================================

async def daily_scheduler():

    logger.info(
        f"🤖 Avtomatik tizim yoqildi."
    )

    logger.info(
        f"⏰ Har kuni "
        f"{SEND_HOUR:02d}:{SEND_MINUTE:02d} "
        f"Toshkent vaqti."
    )

    while True:

        try:

            target = next_run_time()

            now = datetime.now(
                UZ_TZ
            )

            seconds = (
                target - now
            ).total_seconds()

            logger.info(
                f"⏳ Keyingi 50 ta test: "
                f"{target.strftime('%d.%m.%Y %H:%M:%S')}"
            )

            await asyncio.sleep(
                max(seconds, 1)
            )

            await send_daily_tests()

        except asyncio.CancelledError:

            logger.info(
                "Scheduler to'xtatildi."
            )

            break

        except Exception as e:

            logger.exception(
                f"Scheduler xatosi: {e}"
            )

            await asyncio.sleep(
                60
            )


# ============================================================
# /START
# ============================================================

@dp.message(Command("start"))
async def start_command(
    message: types.Message
):

    await message.answer(
        "📚 <b>Tarix Toifa Test Bot</b>\n\n"
        "🤖 Avtomatik tizim: AKTIV\n"
        f"⏰ Har kuni: "
        f"{SEND_HOUR:02d}:{SEND_MINUTE:02d}\n"
        "📝 Kuniga: 50 ta test\n"
        "🎯 Toifa imtihoni darajasi\n\n"
        "/send_now — hozir 50 ta test\n"
        "/status — holat",
        parse_mode="HTML"
    )


# ============================================================
# /SEND_NOW
# ============================================================

@dp.message(Command("send_now"))
async def send_now_command(
    message: types.Message
):

    await message.answer(
        "⏳ 50 ta test tayyorlanmoqda...\n"
        "Bu biroz vaqt olishi mumkin."
    )

    await send_daily_tests()

    await message.answer(
        f"📊 Jarayon tugadi.\n"
        f"Yuborilgan: {today_sent}/50"
    )


# ============================================================
# /STATUS
# ============================================================

@dp.message(Command("status"))
async def status_command(
    message: types.Message
):

    now = datetime.now(
        UZ_TZ
    )

    next_time = next_run_time()

    if last_run_date == now.date():
        today_status = "✅ Bugungi testlar yuborilgan"
    else:
        today_status = "⏳ Bugungi testlar hali yuborilmagan"

    if last_run_time:
        last_time = last_run_time.strftime(
            "%d.%m.%Y %H:%M:%S"
        )
    else:
        last_time = "Hali yo'q"

    await message.answer(
        "📊 <b>BOT HOLATI</b>\n\n"
        f"🇺🇿 Hozirgi vaqt: "
        f"{now.strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"📝 Bugungi holat: {today_status}\n"
        f"📊 Bugun yuborilgan: {today_sent}/50\n"
        f"🕐 Oxirgi yuborish: {last_time}\n"
        f"⏰ Keyingi yuborish: "
        f"{next_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"❌ Xato: "
        f"{last_error or 'Yo‘q'}",
        parse_mode="HTML"
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    await start_web_server()

    scheduler = asyncio.create_task(
        daily_scheduler()
    )

    logger.info(
        "🚀 Telegram bot ishga tushmoqda..."
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        scheduler.cancel()

        try:
            await scheduler
        except asyncio.CancelledError:
            pass

        await bot.session.close()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())




