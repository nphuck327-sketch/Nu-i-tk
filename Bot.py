import os
import time
import requests

TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
URL = f"https://api.telegram.org/bot{TOKEN}/"

def send_message(chat_id, text):
    url = URL + "sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn: {e}")

def get_updates(offset=None):
    url = URL + "getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        response = requests.get(url)
        return response.json()
    except Exception as e:
        print(f"Lỗi lấy cập nhật: {e}")
        return {}

def main():
    print("Bot Python đang khởi động trên Railway...")
    offset = None
    while True:
        updates = get_updates(offset)
        if 'result' in updates:
            for update in updates['result']:
                offset = update['update_id'] + 1
                if 'message' in update and 'text' in update['message']:
                    chat_id = update['message']['chat']['id']
                    text = update['message']['text'].strip()
                    
                    if text == '/start':
                        send_message(chat_id, "🤖 <b>Bot nuôi nick đã sẵn sàng!</b>\n/status - Xem trạng thái\n/run - Chạy tương tác")
                    elif text == '/status':
                        send_message(chat_id, "📊 Hệ thống nuôi nick đang hoạt động ổn định.")
                    elif text == '/run':
                        send_message(chat_id, "⚡ Đang chạy tiến trình tương tác thật...")
        time.sleep(1)

if __name__ == '__main__':
    main()
