# bot.py – Phiên bản sạch sẽ, không system prompt
import telebot
import os
from openai import OpenAI

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("Thiếu TELEGRAM_TOKEN hoặc DEEPSEEK_API_KEY!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1"
)

user_history = {}

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Bot DeepSeek Reasoner đã sẵn sàng!\nGõ gì cũng được, kể cả /clear để xóa lịch sử.")

@bot.message_handler(commands=['clear'])
def clear(message):
    if message.from_user.id in user_history:
        del user_history[message.from_user.id]
    bot.reply_to(message, "Đã xóa lịch sử chat!")

@bot.message_handler(func=lambda m: True)
def reply_all(message):
    user_id = message.from_user.id

    # Nếu chưa có lịch sử thì tạo mới (không có system prompt)
    if user_id not in user_history:
        user_history[user_id] = []  # ← trống hoàn toàn

    # Thêm tin nhắn người dùng vào lịch sử
    user_history[user_id].append({"role": "user", "content": message.text})

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        response = client.chat.completions.create(
            model="deepseek-reasoner",        # hoặc "deepseek-chat" nếu muốn rẻ hơn
            messages=user_history[user_id],
            temperature=0.7,
            max_tokens=4096,
            timeout=60
        )
        reply = response.choices[0].message.content

        # Lưu lại phản hồi để giữ ngữ cảnh cho lần sau
        user_history[user_id].append({"role": "assistant", content: reply)

        # Giữ lịch sử gọn (tối đa ~40 tin gần nhất)
        if len(user_history[user_id]) > 80:  # user + assistant nên để 80 thay vì 40
            user_history[user_id] = user_history[user_id][-80:]

        bot.reply_to(message, reply, parse_mode='MarkdownV2')  # MarkdownV2 ít lỗi hơn
    except Exception as e:
        bot.reply_to(message, f"Lỗi: {e}")

print("Bot đang chạy…")
bot.infinity_polling(none_stop=True, interval=0, timeout=60)
