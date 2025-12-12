import os, logging
import telebot
import httpx
from openai import OpenAI
from html import escape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")
if not DEEPSEEK_API_KEY:
    raise ValueError("Missing DEEPSEEK_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
    http_client=httpx.Client(timeout=60.0),
)

SYSTEM = {"role": "system", "content": "Reply in plain text. Do not use Markdown formatting."}
user_history = {}

def get_key(message):
    return (message.chat.id, message.from_user.id)

@bot.message_handler(func=lambda m: True, content_types=["text"])
def reply_all(message):
    key = get_key(message)
    text = message.text.strip()

    if not text:
        return

    # basic input length guard
    if len(text) > 8000:
        bot.reply_to(message, "Tin nhắn dài quá, bạn rút gọn giúp mình nhé (<= 8000 ký tự).")
        return

    hist = user_history.get(key)
    if not hist:
        hist = [SYSTEM]
        user_history[key] = hist

    hist.append({"role": "user", "content": text})

    bot.send_chat_action(message.chat.id, "typing")

    try:
        resp = client.chat.completions.create(
            model="deepseek-reasoner",
            messages=hist,
            temperature=0.7,
            max_tokens=1024,
        )
        reply = (resp.choices[0].message.content or "").strip()
        hist.append({"role": "assistant", "content": reply})

        # cap history
        if len(hist) > 41:  # 1 system + 40 msgs
            user_history[key] = [SYSTEM] + hist[-40:]

        # send as HTML-safe plain text
        bot.reply_to(message, escape(reply), parse_mode="HTML")

    except Exception as e:
        logger.exception("LLM error")
        bot.reply_to(message, f"Đã xảy ra lỗi: {e}")
