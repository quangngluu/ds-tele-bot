import os
import time
import logging
from collections import deque, defaultdict
from html import escape as html_escape

import telebot
import httpx
from openai import OpenAI

# =========================
# Logging
# =========================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("ds-tele-bot")

# =========================
# Env
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Optional configs
MODEL = os.getenv("MODEL", "deepseek-reasoner")
BASE_URL = os.getenv("BASE_URL", "https://api.deepseek.com/v1")  # note: include /v1
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

# Limits / timeouts
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "8000"))
MAX_TURNS = int(os.getenv("MAX_TURNS", "20"))  # 20 turns => 40 messages (user+assistant)
MAX_COMPLETION_TOKENS = int(os.getenv("MAX_COMPLETION_TOKENS", "1024"))

CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "60"))
WRITE_TIMEOUT = float(os.getenv("WRITE_TIMEOUT", "60"))
POOL_TIMEOUT = float(os.getenv("POOL_TIMEOUT", "60"))

# Simple rate limit: N requests per window seconds per (chat,user)
RL_WINDOW_SEC = int(os.getenv("RL_WINDOW_SEC", "20"))
RL_MAX_REQ = int(os.getenv("RL_MAX_REQ", "6"))

if not TELEGRAM_TOKEN:
    logger.error("Missing TELEGRAM_TOKEN")
    raise ValueError("Missing TELEGRAM_TOKEN")

if not DEEPSEEK_API_KEY:
    logger.error("Missing DEEPSEEK_API_KEY")
    raise ValueError("Missing DEEPSEEK_API_KEY")

# =========================
# Bot + Client
# =========================
bot = telebot.TeleBot(
    TELEGRAM_TOKEN,
    threaded=True,
    num_threads=int(os.getenv("BOT_THREADS", "8")),
)

http_client = httpx.Client(
    timeout=httpx.Timeout(
        READ_TIMEOUT,
        connect=CONNECT_TIMEOUT,
        write=WRITE_TIMEOUT,
        pool=POOL_TIMEOUT,
    )
)

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=BASE_URL,
    http_client=http_client,
)

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful assistant. Reply in plain text.\n"
    "Avoid Markdown formatting characters that might break Telegram.\n"
    "If user writes Vietnamese, reply Vietnamese. If English, reply English.",
)

SYSTEM_MESSAGE = {"role": "system", "content": SYSTEM_PROMPT}

# =========================
# In-memory store
# Key: (chat_id, user_id) -> deque of messages
# =========================
history_store: dict[tuple[int, int], deque] = {}
rate_store: dict[tuple[int, int], deque] = defaultdict(deque)

# =========================
# Helpers
# =========================
def key_of(message) -> tuple[int, int]:
    return (message.chat.id, message.from_user.id)

def get_history(k: tuple[int, int]) -> deque:
    if k not in history_store:
        d = deque()
        d.append(SYSTEM_MESSAGE)
        history_store[k] = d
    return history_store[k]

def trim_history(hist: deque) -> None:
    # keep: 1 system + (MAX_TURNS*2) messages
    max_len = 1 + (MAX_TURNS * 2)
    while len(hist) > max_len:
        # never pop system message
        if len(hist) > 0 and hist[0].get("role") == "system":
            # remove the oldest non-system
            hist.rotate(-1)  # bring next to front
            hist.popleft()
            hist.rotate(1)
        else:
            hist.popleft()

def rate_limited(k: tuple[int, int]) -> bool:
    now = time.time()
    q = rate_store[k]
    # drop old timestamps
    while q and now - q[0] > RL_WINDOW_SEC:
        q.popleft()
    if len(q) >= RL_MAX_REQ:
        return True
    q.append(now)
    return False

def send_html(chat_id: int, text: str) -> None:
    # Telegram HTML parse_mode requires escaping
    bot.send_message(chat_id, html_escape(text), parse_mode="HTML")

def safe_preview(text: str, n: int = 60) -> str:
    if not text:
        return ""
    s = text.replace("\n", " ").strip()
    return (s[:n] + "…") if len(s) > n else s

# =========================
# Commands
# =========================
@bot.message_handler(commands=["start", "help"])
def start(message):
    welcome_text = (
        "🤖 <b>Bot DeepSeek Reasoner</b>\n\n"
        "Chào bạn! Tôi là bot sử dụng DeepSeek Reasoner để trả lời câu hỏi.\n\n"
        "📝 <b>Các lệnh có sẵn:</b>\n"
        "/start hoặc /help - Hiển thị thông tin này\n"
        "/clear - Xóa lịch sử chat\n"
        "/status - Kiểm tra trạng thái bot\n\n"
        "💬 Hãy gửi câu hỏi (text) và tôi sẽ trả lời!"
    )
    send_html(message.chat.id, welcome_text)

@bot.message_handler(commands=["status"])
def status(message):
    k = key_of(message)
    hist = history_store.get(k)
    history_len = max(0, (len(hist) - 1) if hist else 0)  # minus system
    status_text = (
        "✅ Bot đang hoạt động\n"
        f"👤 User ID: {message.from_user.id}\n"
        f"💬 Chat ID: {message.chat.id}\n"
        f"💭 Messages in history: {history_len}\n"
        f"🔧 Model: {MODEL}\n"
        f"🌐 Base URL: {BASE_URL}"
    )
    send_html(message.chat.id, status_text)

@bot.message_handler(commands=["clear"])
def clear(message):
    k = key_of(message)
    if k in history_store:
        deleted = max(0, len(history_store[k]) - 1)  # minus system
        del history_store[k]
        send_html(message.chat.id, f"✅ Đã xóa {deleted} tin nhắn trong lịch sử!")
    else:
        send_html(message.chat.id, "ℹ️ Không có lịch sử nào để xóa.")

# =========================
# Main handler (TEXT only)
# =========================
@bot.message_handler(content_types=["text"])
def reply_all(message):
    k = key_of(message)
    text = (message.text or "").strip()

    if not text:
        return

    # optional: ignore commands here (handled above)
    if text.startswith("/"):
        return

    if rate_limited(k):
        send_html(message.chat.id, "⏳ Bạn gửi nhanh quá. Chờ vài giây rồi thử lại nhé.")
        return

    if len(text) > MAX_INPUT_CHARS:
        send_html(
            message.chat.id,
            f"Tin nhắn dài quá ({len(text)} ký tự). "
            f"Bạn rút gọn giúp mình nhé (<= {MAX_INPUT_CHARS} ký tự)."
        )
        return

    hist = get_history(k)
    hist.append({"role": "user", "content": text})
    trim_history(hist)

    logger.info(
        "IN chat=%s user=%s text='%s'",
        message.chat.id,
        message.from_user.id,
        safe_preview(text),
    )

    bot.send_chat_action(message.chat.id, "typing")

    try:
        logger.info("➡️ Calling model chat=%s user=%s", message.chat.id, message.from_user.id)

        resp = client.chat.completions.create(
            model=MODEL,
            messages=list(hist),
            temperature=TEMPERATURE,
            max_tokens=MAX_COMPLETION_TOKENS,
        )

        reply = (resp.choices[0].message.content or "").strip()
        if not reply:
            reply = "Mình chưa nhận được nội dung phản hồi từ model. Bạn thử lại giúp mình nhé."

        hist.append({"role": "assistant", "content": reply})
        trim_history(hist)

        logger.info(
            "OUT chat=%s user=%s chars=%s",
            message.chat.id,
            message.from_user.id,
            len(reply),
        )

        # Send as HTML-safe text (avoid Markdown issues)
        send_html(message.chat.id, reply)

    except Exception as e:
        logger.exception("❌ LLM error chat=%s user=%s", message.chat.id, message.from_user.id)
        send_html(
            message.chat.id,
            "❌ Đã xảy ra lỗi khi gọi AI.\n"
            f"Chi tiết: {type(e).__name__}: {str(e)}"
        )

# =========================
# Non-text handler
# =========================
@bot.message_handler(content_types=[
    "photo", "sticker", "video", "voice", "audio", "document", "location", "contact"
])
def non_text(message):
    send_html(message.chat.id, "Mình hiện chỉ hỗ trợ tin nhắn dạng <b>text</b> nhé.")

# =========================
# Boot
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting Telegram bot...")
    try:
        import openai as openai_pkg
        logger.info("Versions: openai=%s httpx=%s", getattr(openai_pkg, "__version__", "?"), httpx.__version__)
    except Exception:
        pass

    logger.info("Config: MODEL=%s BASE_URL=%s", MODEL, BASE_URL)

    # skip_pending=True giúp bỏ backlog message cũ khi bot restart
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
