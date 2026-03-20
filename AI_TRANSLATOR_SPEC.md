# AI TRANSLATOR (PHIÊN DỊCH VIÊN)

## 1. Tổng quan

Tính năng cho phép người dùng biên dịch nội dung văn bản thuần túy hoặc tệp phụ đề `.srt` từ ngôn ngữ nguồn sang tiếng Việt bằng DeepSeek hoặc Google Gemini.

## 2. Input / Output

- Input:
  - Văn bản thô
  - Tệp `.srt`
  - Ngôn ngữ nguồn, mặc định `auto`
  - Ngôn ngữ đích, mặc định `vi`
- Output:
  - File `.txt` hoặc `.srt` đã dịch
  - Giữ nguyên thứ tự dòng
  - Với `.srt`, giữ nguyên timestamp và numbering

## 3. Cấu hình

- LLM hỗ trợ:
  - `Google Gemini`
  - `deepseek-chat`
  - `deepseek-reasoner`
- Có thể chọn `Google Gemini`
  - Model được load động từ API sau khi user nhập API key
- API key được nhập ngay trên giao diện local app theo provider đang chọn
- Hệ thống lưu vào `config.json`
- Toàn bộ API key và nơi lưu output được cấu hình trong popup `Settings`
- Với file `.srt`, user chọn thêm giới hạn `ký tự/giây` để prompt ép bản dịch khớp tốt hơn với thời lượng subtitle
- Với Google Gemini:
  - User nhập API key trước
  - Hệ thống gọi API để load model khả dụng
- Các lần sau app tự load lại key, model và preferences

## 4. Tối ưu kỹ thuật

- Batch theo ngữ cảnh 15-20 dòng
- Có giới hạn số ký tự mỗi batch
- Gọi bất đồng bộ với max concurrent request
- Retry khi gặp rate-limit hoặc lỗi tạm thời
- Có checkpoint theo `job_hash`
- Có thể dịch tiếp từ bản dở gần nhất
- Hiển thị estimate token trước khi dịch cho text và SRT
- Với SRT, mỗi subtitle được tính `max_chars = duration_seconds * chars_per_second`
- Giới hạn này chỉ dùng để truyền tải ràng buộc vào prompt; app không tự cắt bản dịch sau khi model trả về

## 5. UX/UI

- Menu trái có mục `Phiên dịch viên`
- Sidebar có nút `⚙ Settings`
- Progress bar hiển thị tiến độ dịch theo batch
- Nút `Dừng` nằm cạnh progress bar
- Log riêng cho phiên dịch
- Có nút `Dịch tiếp bản gần nhất`
- Có lựa chọn `ký tự/giây` cho SRT, mặc định `32`

## 6. Prompting

- Ưu tiên âm Hán Việt cho tên riêng/thuật ngữ cổ phong khi phù hợp
- Không phá cấu trúc SRT
- Trả về JSON để app ghép lại an toàn
- Với SRT, prompt yêu cầu từng mục cố gắng không vượt quá `max_chars` theo thời lượng subtitle nhưng vẫn phải giữ câu tự nhiên, liền mạch
