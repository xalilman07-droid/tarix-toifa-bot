import os
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from google import genai
from google.genai import types as genai_types


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# ENV VARIABLES
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

SEND_HOUR = int(os.getenv("SEND_HOUR", "8"))
SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))

DAILY_TEST_COUNT = int(os.getenv("DAILY_TEST_COUNT", "50"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

# Telegram poll yuborish oralig'i.
# Xavfsizroq ishlashi uchun 3 soniya.
SEND_DELAY = float(os.getenv("SEND_DELAY", "3"))

# Toshkent vaqti
UZ_TZ = ZoneInfo("Asia/Tashkent")

# Holat saqlanadigan fayl
STATE_FILE = Path("bot_state.json")


# ============================================================
# ENVIRONMENT TEKSHIRISH
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi!")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY topilmadi!")

if not TARGET_CHAT_ID:
    raise RuntimeError("TARGET_CHAT_ID topilmadi!")

if DAILY_TEST_COUNT <= 0:
    raise RuntimeError("DAILY_TEST_COUNT 0 dan katta bo'lishi kerak!")

if BATCH_SIZE <= 0:
    raise RuntimeError("BATCH_SIZE 0 dan katta bo'lishi kerak!")


# ============================================================
# BOT VA GEMINI
# ============================================================

bot = Bot(token=BOT_TOKEN)

# Dispatcher qoldiriladi.
# Hozir buyruq ishlatmasak ham polling barqaror ishlashi uchun.
dp = Dispatcher()

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# GLOBAL HOLAT
# ============================================================

# Bir vaqtda faqat BITTA yuborish ishlashi uchun.
send_lock = asyncio.Lock()

# Hozir yuborish jarayoni ketayotganini ko'rsatadi.
is_sending = False

# Statistika
today_sent = 0
last_run_date = None
last_run_time = None
last_error = None


# ============================================================
# STATE SAQLASH
# ============================================================

def load_state():
    global today_sent
    global last_run_date
    global last_run_time
    global last_error

    if not STATE_FILE.exists():
        logger.info("State fayli hali mavjud emas.")
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        last_date = data.get("last_run_date")
        last_time = data.get("last_run_time")

        if last_date:
            last_run_date = datetime.fromisoformat(
                last_date
            ).date()

        if last_time:
            last_run_time = datetime.fromisoformat(
                last_time
            )

        today_sent = int(
            data.get("today_sent", 0)
        )

        last_error = data.get("last_error")

        logger.info(
            "Avvalgi bot holati yuklandi."
        )

    except Exception as error:
        logger.exception(
            f"State yuklashda xato: {error}"
        )


def save_state():
    try:
        data = {
            "last_run_date": (
                last_run_date.isoformat()
                if last_run_date
                else None
            ),

            "last_run_time": (
                last_run_time.isoformat()
                if last_run_time
                else None
            ),

            "today_sent": today_sent,

            "last_error": last_error
        }

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        logger.exception(
            f"State saqlashda xato: {error}"
        )


# ============================================================
# RENDER WEB SERVER
# ============================================================

async def home(request):

    return web.Response(
        text=(
            "Tarix Toifa Test Bot ishlayapti! "
            "Avtomatik rejim: AKTIV"
        )
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

    return runner


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
                        },
                        "minItems": 4,
                        "maxItems": 4
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

Quyidagi davrlarni muvozanatli aralashtir:
- qadimgi davr
- ilk va rivojlangan o'rta asrlar
- Amir Temur va Temuriylar
- xonliklar
- Rossiya imperiyasi davri
- jadidchilik
- sovet davri
- mustaqillik davri
"""

    else:

        topic_instruction = """
Bu blokda JAHON TARIXI ustun bo'lsin.

Quyidagi davrlarni muvozanatli aralashtir:
- Qadimgi Sharq
- Yunoniston
- Rim
- o'rta asrlar
- Uyg'onish
- Buyuk geografik kashfiyotlar
- sanoat inqilobi
- XVIII-XIX asrlar
- Birinchi jahon urushi
- Ikkinchi jahon urushi
- XX asr tarixi
"""


    return f"""
SEN O'zbekiston Respublikasidagi tarix fanidan
TOIFA IMTIHONI uchun professional test tuzuvchi ekspertisan.

{BATCH_SIZE} ta YANGI va BIR-BIRIDAN FARQLI
tarix testini yarat.

Bu {batch_number}-blok.

{topic_instruction}

MUHIM TALABLAR:

1. Savollar tarix fanidan TOIFA IMTIHONI darajasida bo'lsin.

2. Oddiy yodlash savollaridan ko'ra quyidagilarga ustuvorlik ber:
   - sabab va oqibat
   - xronologik tahlil
   - voqealarni taqqoslash
   - tarixiy jarayonlarni bog'lash
   - davlat siyosati
   - islohotlar
   - tarixiy hujjatlar
   - tarixiy shaxslar faoliyatini tahlil qilish

3. Tarixiy faktlarni uydirma qilma.

4. Sana, ism, joy va voqealarni imkon qadar aniq yoz.

5. Har bir savolda aynan 4 ta variant bo'lsin.

6. Variantlar A, B, C, D deb yozilmasin.
   Faqat variant matnlarini massivga joylashtir.

7. Faqat BITTA to'g'ri javob bo'lsin.

8. correct_option_id qiymati:
   0 = birinchi variant
   1 = ikkinchi variant
   2 = uchinchi variant
   3 = to'rtinchi variant

9. Noto'g'ri variantlar mantiqan ishonarli bo'lsin.

10. To'g'ri javob uzunligi yoki grammatikasi orqali
    juda oson bilinib qolmasin.

11. Savollar bir-birini takrorlamasin.

12. Bir xil tarixiy voqeani turli so'zlar bilan
    qayta savol qilma.

13. Savol 300 belgidan oshmasin.

14. Har bir variant 100 belgidan oshmasin.

15. explanation 200 belgidan oshmasin.

16. explanation juda muhim:
    foydalanuvchi javob bergandan keyin
    nima uchun aynan shu javob to'g'ri ekanini
    qisqa va tushunarli tarzda izohlasin.

17. Testlar sof va tushunarli o'zbek adabiy tilida bo'lsin.

18. Faqat JSON qaytar.

JSON formati:

{{
  "tests": [
    {{
      "question": "Savol matni",
      "options": [
        "1-variant",
        "2-variant",
        "3-variant",
        "4-variant"
      ],
      "correct_option_id": 0,
      "explanation": "To'g'ri javobning qisqa tarixiy izohi"
    }}
  ]
}}

Aynan {BATCH_SIZE} ta test yarat.
"""


# ============================================================
# TEST VALIDATSIYASI
# ============================================================

def validate_test(item):

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
            return None


        options = [
            str(option).strip()
            for option in options
        ]


        if len(question) < 10:
            return None

        if len(question) > 300:
            return None


        if len(options) != 4:
            return None


        if any(
            len(option) < 1
            or len(option) > 100
            for option in options
        ):
            return None


        if len(set(
            option.lower()
            for option in options
        )) != 4:
            return None


        if correct_id not in (
            0,
            1,
            2,
            3
        ):
            return None


        if len(explanation) < 5:
            return None


        explanation = explanation[:200]


        return {
            "question": question,
            "options": options,
            "correct_option_id": correct_id,
            "explanation": explanation
        }


    except Exception:

        return None


# ============================================================
# GEMINI'DAN BATCH OLISH
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


    if not isinstance(
        tests,
        list
    ):

        raise ValueError(
            "Gemini 'tests' massivini qaytarmadi!"
        )


    valid_tests = []


    for item in tests:

        test = validate_test(
            item
        )

        if test:

            valid_tests.append(
                test
            )


    logger.info(
        f"{batch_number}-blokdan "
        f"{len(valid_tests)} ta sifatli test olindi."
    )


    return valid_tests


# ============================================================
# TAKRORLANISHLARNI TOZALASH
# ============================================================

def normalize_question(text):

    text = text.lower()

    text = re.sub(
        r"[^a-zA-Z0-9ʻ’'ʻʼo‘g‘ ]",
        "",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


def remove_duplicates(tests):

    unique_tests = []

    seen = set()


    for test in tests:

        key = normalize_question(
            test["question"]
        )


        if not key:
            continue


        if key in seen:
            continue


        seen.add(
            key
        )

        unique_tests.append(
            test
        )


    return unique_tests


# ============================================================
# KERAKLI MIQDORDA TEST YARATISH
# ============================================================

async def generate_daily_tests():

    all_tests = []

    batches_needed = (
        DAILY_TEST_COUNT
        + BATCH_SIZE
        - 1
    ) // BATCH_SIZE


    max_extra_batches = 5


    for batch_number in range(
        1,
        batches_needed + max_extra_batches + 1
    ):

        if len(all_tests) >= DAILY_TEST_COUNT:
            break


        success = False


        for attempt in range(1, 4):

            try:

                tests = await generate_batch(
                    batch_number
                )

                all_tests.extend(
                    tests
                )

                all_tests = remove_duplicates(
                    all_tests
                )

                success = True

                break


            except Exception as error:

                logger.exception(
                    f"{batch_number}-blok, "
                    f"{attempt}-urinish xatosi: "
                    f"{error}"
                )


                if attempt < 3:

                    await asyncio.sleep(
                        attempt * 5
                    )


        if not success:

            logger.warning(
                f"{batch_number}-blok olinmadi."
            )


        logger.info(
            f"Hozircha noyob testlar: "
            f"{len(all_tests)}/"
            f"{DAILY_TEST_COUNT}"
        )


    if len(all_tests) < DAILY_TEST_COUNT:

        raise RuntimeError(
            f"Yetarli test yaratilmadi. "
            f"{len(all_tests)}/"
            f"{DAILY_TEST_COUNT}"
        )


    return all_tests[
        :DAILY_TEST_COUNT
    ]


# ============================================================
# BITTA TESTNI XAVFSIZ YUBORISH
# ============================================================

async def send_one_test(
    test,
    number
):

    max_attempts = 5


    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            await bot.send_poll(

                chat_id=TARGET_CHAT_ID,

                question=test["question"],

                options=test["options"],

                type="quiz",

                correct_option_id=test[
                    "correct_option_id"
                ],

                # FOYDALANUVCHI JAVOB BERGACH
                # Telegram shu izohni ko'rsatadi.
                explanation=test[
                    "explanation"
                ],

                is_anonymous=True
            )


            logger.info(
                f"✅ {number}/"
                f"{DAILY_TEST_COUNT} "
                f"test yuborildi."
            )


            return True


        except TelegramRetryAfter as error:

            retry_seconds = int(
                error.retry_after
            ) + 2


            logger.warning(
                f"⚠️ Flood Control! "
                f"{retry_seconds} soniya "
                f"kutilmoqda..."
            )


            await asyncio.sleep(
                retry_seconds
            )


        except TelegramBadRequest as error:

            logger.error(
                f"❌ {number}-test "
                f"Telegram tomonidan rad etildi: "
                f"{error}"
            )

            return False


        except Exception as error:

            logger.error(
                f"❌ {number}-test, "
                f"{attempt}-urinish xatosi: "
                f"{error}"
            )


            if attempt < max_attempts:

                await asyncio.sleep(
                    attempt * 5
                )


    logger.error(
        f"❌ {number}-test "
        f"{max_attempts} urinishdan keyin "
        f"yuborilmadi."
    )


    return False


# ============================================================
# KUNLIK TESTLARNI YUBORISH
# ============================================================

async def send_daily_tests():

    global is_sending
    global today_sent
    global last_run_date
    global last_run_time
    global last_error


    # BIR VAQTNING O'ZIDA FAQAT BITTA
    # YUBORISH JARAYONI BO'LISHI MUMKIN.
    async with send_lock:


        if is_sending:

            logger.warning(
                "Yuborish allaqachon davom etmoqda."
            )

            return


        is_sending = True


        try:

            now = datetime.now(
                UZ_TZ
            )

            today = now.date()


            # Bugun muvaffaqiyatli yuborilgan bo'lsa
            # ikkinchi marta yubormaydi.
            if (
                last_run_date == today
                and today_sent >= DAILY_TEST_COUNT
            ):

                logger.info(
                    "Bugungi testlar allaqachon "
                    "to'liq yuborilgan."
                )

                return


            logger.info(
                "========================================"
            )

            logger.info(
                "🚀 KUNLIK TESTLAR "
                "TAYYORLANMOQDA"
            )

            logger.info(
                f"📅 Sana: {today}"
            )

            logger.info(
                "========================================"
            )


            # Yangi kun uchun hisobni boshlash
            today_sent = 0

            save_state()


            tests = await generate_daily_tests()


            logger.info(
                f"{len(tests)} ta test tayyor."
            )


            sent_count = 0


            for index, test in enumerate(
                tests,
                start=1
            ):

                success = await send_one_test(
                    test,
                    index
                )


                if success:

                    sent_count += 1

                    today_sent = sent_count

                    # Har muvaffaqiyatli yuborishdan keyin
                    # holat saqlanadi.
                    save_state()


                # Keyingi testgacha xavfsiz pauza
                if index < len(tests):

                    await asyncio.sleep(
                        SEND_DELAY
                    )


            # Faqat hammasi muvaffaqiyatli yuborilganda
            # kun bajarildi deb hisoblanadi.
            if sent_count >= DAILY_TEST_COUNT:

                last_run_date = today

                last_run_time = datetime.now(
                    UZ_TZ
                )

                last_error = None

                logger.info(
                    "🎉 BUGUNGI YUBORISH "
                    f"YAKUNLANDI: "
                    f"{sent_count}/"
                    f"{DAILY_TEST_COUNT}"
                )


            else:

                last_error = (
                    f"Faqat {sent_count}/"
                    f"{DAILY_TEST_COUNT} "
                    f"test yuborildi."
                )

                logger.error(
                    f"⚠️ Yuborish to'liq tugamadi: "
                    f"{last_error}"
                )


            save_state()


        except Exception as error:

            last_error = str(
                error
            )

            save_state()

            logger.exception(
                f"❌ Kundalik testlarda "
                f"xato: {error}"
            )


        finally:

            is_sending = False


# ============================================================
# KEYINGI YUBORISH VAQTINI TOPISH
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
# STARTUP PAYTIDA O'TKAZIB YUBORILGAN
# BUGUNGI ISHNI TEKSHIRISH
# ============================================================

async def check_missed_run():

    global last_run_date


    now = datetime.now(
        UZ_TZ
    )

    today = now.date()


    scheduled_time = now.replace(

        hour=SEND_HOUR,

        minute=SEND_MINUTE,

        second=0,

        microsecond=0
    )


    # Agar bot belgilangan vaqtdan keyin ishga tushgan bo'lsa
    # va bugungi testlar hali to'liq yuborilmagan bo'lsa,
    # avtomatik yuborishni boshlaydi.
    if (
        now >= scheduled_time
        and last_run_date != today
    ):

        logger.info(
            "Bugungi rejalashtirilgan yuborish "
            "hali bajarilmagan."
        )

        asyncio.create_task(
            send_daily_tests()
        )


# ============================================================
# AVTOMATIK SCHEDULER
# ============================================================

async def daily_scheduler():

    logger.info(
        "🤖 Avtomatik tizim yoqildi."
    )

    logger.info(
        f"⏰ Har kuni "
        f"{SEND_HOUR:02d}:"
        f"{SEND_MINUTE:02d} "
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
                f"⏳ Keyingi yuborish: "
                f"{target.strftime('%d.%m.%Y %H:%M:%S')}"
            )


            await asyncio.sleep(
                max(seconds, 1)
            )


            # Hech qanday buyruqsiz
            # avtomatik yuborish.
            await send_daily_tests()


        except asyncio.CancelledError:

            logger.info(
                "Scheduler to'xtatildi."
            )

            break


        except Exception as error:

            logger.exception(
                f"Scheduler xatosi: "
                f"{error}"
            )

            await asyncio.sleep(
                60
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "📚 TARIX TOIFA TEST BOT"
    )

    logger.info(
        "🤖 Avtomatik rejim"
    )

    logger.info(
        f"📝 Kunlik testlar: "
        f"{DAILY_TEST_COUNT}"
    )

    logger.info(
        "========================================"
    )


    # Avvalgi holatni yuklash
    load_state()


    # Render web server
    web_runner = await start_web_server()


    # Scheduler
    scheduler_task = asyncio.create_task(
        daily_scheduler()
    )


    # Startup vaqtida o'tkazib yuborilgan
    # kunlik ishni tekshirish.
    await check_missed_run()


    logger.info(
        "🚀 Bot to'liq avtomatik rejimda ishga tushdi."
    )


    try:

        # Hech qanday command handler bo'lmasa ham
        # polling botni ishlatib turadi.
        await dp.start_polling(
            bot
        )


    finally:

        logger.info(
            "Bot to'xtatilmoqda..."
        )


        scheduler_task.cancel()


        try:

            await scheduler_task

        except asyncio.CancelledError:

            pass


        await bot.session.close()


        await web_runner.cleanup()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )















