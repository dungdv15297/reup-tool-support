# Local Multi Provider TTS Spec

## 1. Phạm vi

Tool chỉ chạy bằng local desktop app.

Web app đã bị loại bỏ khỏi phạm vi sản phẩm. Toàn bộ luồng cấu hình, generate, preview và theo dõi tiến trình đều thực hiện trực tiếp trong ứng dụng local.

Feature này trong tool được đặt tên là `Thuyết minh viên` và nằm ở menu bên trái.

Các cấu hình dùng chung như API key và nơi lưu file output nằm trong popup `Settings`, không cấu hình lại trong màn hình tính năng.

## 2. Provider được hỗ trợ

- `Edge TTS`
- `Google Cloud TTS`
- `Amazon Polly`
- `Vbee`

## 3. Chức năng người dùng

### 3.1. Chọn công cụ TTS

User có thể chọn:

- Provider
- Voice
- Tốc độ đọc từ `0.9x` đến `2.0x`, bước nhảy `0.1`

Giao diện:

- Menu trái có mục `Thuyết minh viên`
- Sidebar có nút `⚙ Settings` để mở popup cấu hình
- Khu settings phải gọn, ưu tiên nhường diện tích cho log phiên
- Log phiên phải hiển thị được nhiều nội dung hơn so với phần settings

App không còn lựa chọn `phong cách đọc` trong tính năng này.

### 3.2. Nghe thử voice

User có thể nhập nội dung nghe thử riêng và phát preview ngay trong local app.

Preview phải dùng đúng:

- Provider đang chọn
- Voice đang chọn
- Tốc độ đang chọn

### 3.3. Generate từ text

User nhập văn bản thường và xuất file MP3.

Tên file mặc định:

- `tts_output_ddMMyy_hhmmss.mp3`

App hiển thị estimate thời gian đọc theo tốc độ đã chọn trước khi generate.

### 3.4. Generate từ SRT

User chọn file `.srt` và xuất file MP3 đã căn timeline.

Tên file mặc định:

- `tts_output_ddMMyy_hhmmss.mp3`

Luồng xử lý:

- Parse SRT
- Clean HTML tags
- Chỉ lấy phần nội dung thoại, không đọc số thứ tự block hoặc dòng timestamp nếu chúng xuất hiện trong text
- Tạo audio cho từng block
- Nếu `audio_len > sub_duration` thì tự tăng tốc độ đọc của block đó và synthesize lại
- Tự ước lượng tốc độ cần thiết ngay từ đầu cho block có nguy cơ bị tràn để giảm số lần synthesize lại
- Retry khi provider lỗi tạm thời
- Concurrency được tối ưu theo từng provider để tăng tốc xử lý toàn job
- Chèn silence theo timeline
- Ghép file MP3 cuối

App hiển thị:

- Estimate thời gian đọc của toàn bộ lời thoại
- Timeline gốc của file SRT để user đối chiếu

## 4. Quản lý credentials trong local app

Các provider cần cấu hình phải nhập trực tiếp trên giao diện local app, không bắt user tự export env mỗi lần mở tool.

Thông tin cần nhập tại UI:

### 4.1. Google Cloud TTS

- `Google API key`

### 4.2. Amazon Polly

- `AWS access key id`
- `AWS secret access key`
- `AWS region`

### 4.3. Vbee

- `Vbee API token`
- `Vbee TTS URL`
- `Vbee app id` nếu có
- `Vbee response mode`
- `Vbee voices json`

## 5. Lưu trạng thái local

App phải lưu toàn bộ state vào file JSON cục bộ để user không cần nhập lại nhiều lần.

File state:

- `config.json`

Nội dung cần lưu:

- Credentials/provider settings
- Provider gần nhất
- Voice gần nhất
- Tốc độ đọc gần nhất
- Text input gần nhất
- Preview text gần nhất
- File SRT gần nhất
- Log của phiên gần nhất

## 6. Hành vi khi mở app lại

Khi local app khởi động lại, app phải:

- Load lại credentials từ `config.json`
- Áp dụng credentials vào runtime
- Load lại provider, voice, tốc độ gần nhất
- Load lại text input và preview text gần nhất
- Load lại file SRT gần nhất nếu file vẫn còn tồn tại
- Load lại log phiên gần nhất

## 7. Hiệu năng và ổn định

Các cơ chế bắt buộc:

- Progress hiển thị theo stage
- Nút `Dừng` nằm cạnh progress bar để user có thể dừng nhanh
- Retry tối đa `3` lần với backoff tăng dần
- Xử lý SRT theo concurrency có giới hạn
- Báo lỗi rõ ràng trong phiên
- Dọn file tạm sau khi generate

Progress cần thể hiện được ít nhất:

- Khởi tạo
- Gọi provider
- Đang tạo block `x/y`
- Đang ghép audio
- Hoàn tất hoặc lỗi

## 8. Ràng buộc hiện tại

- `Amazon Polly` hiện không có voice `vi-VN` chính thức.
- `Google Cloud TTS` trong app này dùng REST API với `API key` để giảm độ khó cấu hình cho user.
- `Google Cloud TTS`, `Amazon Polly`, `Vbee` chỉ chạy thật khi credentials hợp lệ.
- `Vbee` có thể cần tinh chỉnh payload/response tùy cấu hình API thực tế của tài khoản.

## 9. File chính

- `gui_app.py`: local app UI
- `tts_service.py`: core multi-provider, preview, retry, progress, SRT processing
- `app_state.py`: load/save JSON state
- `config.json`: file dữ liệu local được sinh ra khi app chạy
