import telebot
import os
import logging
from openai import OpenAI

# Cấu hình logging để debug trên Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Kiểm tra biến môi trường
if not TELEGRAM_TOKEN:
    logger.error("❌ Thiếu biến môi trường TELEGRAM_TOKEN")
    raise ValueError("Thiếu TELEGRAM_TOKEN!")
    
if not DEEPSEEK_API_KEY:
    logger.error("❌ Thiếu biến môi trường DEEPSEEK_API_KEY")
    raise ValueError("Thiếu DEEPSEEK_API_KEY!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

from openai import OpenAI

# Initialize the DeepSeek client - SIMPLIFIED VERSION
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

user_history = {}

@bot.message_handler(commands=['start', 'help'])
def start(message):
    welcome_text = """
🤖 *Bot DeepSeek Reasoner*

Chào bạn! Tôi là bot sử dụng DeepSeek Reasoner để trả lời câu hỏi.

📝 *Các lệnh có sẵn:*
/start hoặc /help - Hiển thị thông tin này
/clear - Xóa lịch sử chat
/status - Kiểm tra trạng thái bot

💬 Hãy gửi câu hỏi của bạn và tôi sẽ trả lời!
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def status(message):
    user_id = message.from_user.id
    history_len = len(user_history.get(user_id, []))
    status_text = f"""
✅ Bot đang hoạt động bình thường
👤 ID của bạn: {user_id}
💭 Số tin nhắn trong lịch sử: {history_len}
🔧 Model: DeepSeek Reasoner
    """
    bot.reply_to(message, status_text)

@bot.message_handler(commands=['clear'])
def clear(message):
    user_id = message.from_user.id
    if user_id in user_history:
        deleted_count = len(user_history[user_id])
        del user_history[user_id]
        bot.reply_to(message, f"✅ Đã xóa {deleted_count} tin nhắn trong lịch sử!")
    else:
        bot.reply_to(message, "ℹ️ Không có lịch sử nào để xóa.")

@bot.message_handler(func=lambda m: True)
def reply_all(message):
    user_id = message.from_user.id
    logger.info(f"📥 Nhận tin nhắn từ user {user_id}: {message.text[:50]}...")

    # Nếu chưa có lịch sử thì tạo mới
    if user_id not in user_history:
        user_history[user_id] = []

    # Thêm tin nhắn người dùng vào lịch sử
    user_history[user_id].append({"role": "user", "content": message.text})

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        response = client.chat.completions.create(
            model="deepseek-reasoner",
            messages=user_history[user_id],
            temperature=0.7,
            max_tokens=4096,
            timeout=60
        )
        
        reply = response.choices[0].message.content
        
        # Lưu lại phản hồi
        user_history[user_id].append({"role": "assistant", "content": reply})
        
        # Giới hạn lịch sử (giữ 20 lượt chat gần nhất = 40 tin nhắn)
        if len(user_history[user_id]) > 40:
            user_history[user_id] = user_history[user_id][-40:]
        
        logger.info(f"📤 Phản hồi cho user {user_id}: {len(reply)} ký tự")
        
        # Gửi reply với Markdown, nếu lỗi thì gửi plain text
        try:
            bot.reply_to(message, reply, parse_mode='Markdown')
        except Exception as md_error:
            logger.warning(f"Markdown error, sending plain text: {md_error}")
            bot.reply_to(message, reply, parse_mode=None)
            
    except Exception as e:
        logger.error(f"❌ Lỗi khi xử lý tin nhắn: {e}")
        error_msg = f"❌ Đã xảy ra lỗi:\n\n`{str(e)}`\n\nVui lòng thử lại sau!"
        bot.reply_to(message, error_msg, parse_mode='Markdown')

if __name__ == "__main__":
    logger.info("🚀 Khởi động bot Telegram...")
    logger.info(f"🤖 Bot token: {'Đã cấu hình' if TELEGRAM_TOKEN else 'CHƯA CÓ'}")
    logger.info(f"🔑 DeepSeek API: {'Đã cấu hình' if DEEPSEEK_API_KEY else 'CHƯA CÓ'}")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"❌ Bot dừng do lỗi: {e}")
