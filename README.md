# Reup Tool Support - Local Multi Provider TTS

Tool local app để chuyển văn bản hoặc file `.srt` thành giọng nói với nhiều provider. Feature này trong tool được đặt tên là `Thuyết minh viên` và xuất hiện dưới dạng menu bên trái.

Tool hiện có thêm module `Phiên dịch viên` để dịch văn bản hoặc `.srt` sang tiếng Việt bằng DeepSeek hoặc Google Gemini.

- `Edge TTS`
- `Google Cloud TTS`
- `Amazon Polly`
- `Vbee`

## Tính năng chính

- Chỉ chạy bằng local desktop app
- Có nút `⚙ Settings` mở popup cấu hình dùng chung
- Feature hiển thị dưới tên `Thuyết minh viên` ở menu trái
- Có thêm menu `Phiên dịch viên` để dịch text/SRT bằng AI
- `Phiên dịch viên` nằm trên `Thuyết minh viên` ở menu trái
- Với Google Gemini, user nhập API key trước rồi bấm load model trực tiếp từ API
- Module dịch hiển thị estimate token cho text và SRT trước khi chạy
- Chọn provider, voice và tốc độ đọc từ `0.9x` đến `2.0x`
- Nghe thử voice trước khi generate
- Generate từ text hoặc từ file `.srt`
- Với `.srt`, app chỉ đọc phần nội dung thoại và giữ timeline theo file gốc
- Nếu audio của một subtitle dài hơn `End Time`, app sẽ tự tăng tốc block đó để giảm tràn timeline
- Luồng SRT được tối ưu bằng concurrency theo provider và ước lượng tốc độ block trước khi gọi TTS để giảm thời gian chờ
- Hiển thị estimate thời gian đọc cho text và SRT theo tốc độ đã chọn
- Có progress, retry và log phiên
- Nút `Dừng` nằm cạnh thanh tiến trình của từng feature
- Tự lưu `credentials`, lựa chọn gần nhất và log phiên gần nhất vào file JSON cục bộ
- Tên file MP3 mặc định theo format `tts_output_ddMMyy_hhmmss`
- Lưu cấu hình vào `config.json`, bao gồm API key cho translator và checkpoint resume của phần dịch

## Cài đặt

1. Cài Python `3.9+`
2. Cài `FFmpeg`
3. Cài dependencies:

```bash
pip3 install -r requirements.txt
```

## Chạy app

```bash
python3 gui_app.py
```

## File quan trọng

- `gui_app.py`: local desktop app
- `tts_service.py`: core multi-provider TTS
- `app_state.py`: lưu state/credentials/session vào JSON
- `TTS_MULTI_PROVIDER_SPEC.md`: file spec tính năng

## Ghi chú

- File state local được lưu tại `config.json` trong thư mục project.
- `Google Cloud TTS` trong app này dùng `API key` để đơn giản hóa cấu hình cho user.
- `Amazon Polly` hiện không có voice tiếng Việt chính thức.
- `Vbee` cần cấu hình đúng endpoint và catalog voice theo tài khoản thực tế.
