# ĐẶC TẢ YÊU CẦU DỰ ÁN (REQUIREMENTS.MD)
## Tên Tool: Auto Translator & TTS (Local AI)

### 1. MÔ TẢ TỔNG QUAN
Tool cung cấp giải pháp tự động hóa quy trình phiên dịch và lồng tiếng (Voiceover) cho kịch bản/phụ đề. Hệ thống tận dụng sức mạnh của Local LLM và API TTS để tối ưu chi phí, lách bộ lọc kiểm duyệt và tăng tốc độ sản xuất video.

Hệ thống cung cấp 2 resource độc lập và nối tiếp nhau:
1. Phiên dịch viên
2. Thuyết minh viên

---

### 2. LUỒNG NGƯỜI DÙNG (USER FLOW) & INPUT
Người dùng tương tác qua giao diện chính với các trường Input sau:
- **Nguồn dữ liệu đầu vào:** Tải lên file `.srt` hoặc `.txt`.
- **Lựa chọn "Não" AI (LLM Model):** Dropdown cho phép user chọn model local đang có sẵn trên máy (ví dụ: `qwen2.5:14b`, `llama3`).
- **Nơi lưu Output:** Chọn thư mục đích để xuất file sau khi hoàn thành.

---

### 3. RESOURCE 1: PHIÊN DỊCH VIÊN
Chịu trách nhiệm phân tích ngữ cảnh, chuyển ngữ và định hình văn phong kịch bản.

**Tùy chọn trên Giao diện:**
- **Ngôn ngữ Nguồn:** Dropdown list (Giá trị mặc định: `Tự phát hiện / Auto-detect`).
- **Ngôn ngữ Đích:** Dropdown list (Giá trị mặc định: `Tiếng Việt`).

**Yêu cầu Logic & Xử lý Text:**
- **Tối ưu hóa (Chunking):** Gộp các đoạn text/dòng SRT gần nhau thành các khối (block/chunk) lớn trước khi gửi cho LLM. Điều này giúp LLM nắm bắt được toàn bộ ngữ cảnh đoạn hội thoại và tăng tối đa tốc độ dịch do giảm số lượng request.
- **Bám sát ngữ cảnh (Context-Aware):** Dịch chính xác nghĩa gốc, tuyệt đối không "ảo tưởng" (hallucinate) tự bịa thêm chi tiết làm sai lệch timeline của phụ đề.
- **Văn phong Hiện đại (GenZ Tone):** Cấu hình Prompt ẩn để LLM chủ động suy luận, sử dụng từ ngữ mượt mà, linh hoạt. Thỉnh thoảng cài cắm tinh tế các từ vựng trending, phong cách GenZ để bắt kịp thời đại, tránh kiểu dịch máy móc, khô khan.

---

### 4. RESOURCE 2: THUYẾT MINH VIÊN
Chịu trách nhiệm chuyển đổi file output của Phiên dịch viên thành file âm thanh (Audio).

**Tùy chọn trên Giao diện (Voice & Style Presets):**
User có thể chọn 1 trong các Phong cách đọc (đã được map ngầm với các Voice ID và thông số Rate/Pitch tương ứng):
- **X - Quảng cáo (Commercial):** Tươi sáng, rõ ràng, dứt khoát, năng lượng cao.
- **Y - Review Phim (Recap):** Dồn dập, lôi cuốn, nhấn nhá mạnh ở các cú twist.
- **Z - Storytelling (Kể chuyện):** Trầm tĩnh, ma mị, ngắt nghỉ dài tạo sự tò mò, hồi hộp.

**Yêu cầu Logic & Xử lý Audio:**
- Đọc chuẩn xác 100% theo nội dung đã được xử lý từ Phiên dịch viên.
- Đồng bộ mốc thời gian (với file SRT) dựa trên các block đã được chunking ở bước trước.

**Yêu cầu chung:**
- Các setup người dùng chọn cần được lưu trữ lại bằng json để không phải chọn lại nhiều lần
---

### 5. ĐỊNH DẠNG OUTPUT
Hệ thống sẽ kết xuất đúng 2 file vào thư mục mà User đã chọn ở bước Input. 
Tên file được tự động sinh ra dựa trên thời gian thực lúc bắt đầu chạy tool, theo định dạng `ddmm_hhmmss`.

1. **File Văn bản (Dịch thuật):** `o_translate_ddmm_hhmmss.srt` (hoặc `.txt` tương ứng với input).
2. **File Âm thanh (Lồng tiếng):** `o_tts_ddmm_hhmmss.mp3`.

### 6. README
Bổ sung README cho tool, bao gồm cách cài đặt - chạy dự án - cách cài đặt - chạy các llm

### 7. MODULE BỔ SUNG: QUẢN LÝ TÀI NGUYÊN AI (1-CLICK INSTALLER)
Module chịu trách nhiệm tự động hóa việc tải và cài đặt các mô hình ngôn ngữ lớn (LLM) chạy local trực tiếp từ giao diện (GUI), loại bỏ hoàn toàn việc người dùng phải thao tác bằng dòng lệnh (Terminal).

**7.1. Giao diện (UI/UX - Renderer Process):**
- **Trạng thái Model:** Hiển thị danh sách các model đang có sẵn trên máy (Fetch từ API `localhost:11434/api/tags`).
- **Nút "Tải Dữ Liệu AI":** Kích hoạt luồng tải model mới (VD: `qwen2.5:14b`). Nút này bị vô hiệu hóa (Disabled) trong lúc đang tải.
- **Thanh Tiến độ (Progress Bar):**
    - Cập nhật Real-time (thời gian thực) chiều dài thanh tiến độ dựa trên dữ liệu stream trả về.
    - Text hiển thị tiến độ tải bắt buộc phải được format hiển thị phần trăm với 1 chữ số thập phân để đảm bảo độ chính xác và đồng nhất (VD: `Đang tải... 15.4%`).
    - Trạng thái chữ: "Đang tải...", "Đang giải nén...", "Sẵn sàng".

**7.2. Yêu cầu Logic (Backend / Main Process):**
- **Kiểm tra Môi trường (Pre-check):** - Khi khởi động app, hệ thống tự động chạy lệnh ngầm kiểm tra xem lõi Ollama (`ollama -v`) đã được cài trên máy tính hay chưa.
    - Nếu chưa có: Khóa nút tải model và hiển thị thông báo hướng dẫn user cài đặt Ollama Core trước.
- **Xử lý Luồng tải (Download Streaming):**
    - Gọi API `/api/pull` của Ollama.
    - Bắt luồng dữ liệu trả về liên tục (Stream Decoder), bóc tách JSON để lấy các tham số `completed` và `total`.
    - Tính toán tỷ lệ phần trăm và bắn sự kiện (`ipcMain`) liên tục về cho UI để cập nhật thanh tiến độ không bị giật lag.

**7.3. Xử lý Ngoại lệ (Error Handling):**
- **Mất mạng giữa chừng:** Bắt sự kiện Timeout hoặc Network Error. Hiển thị thông báo "Lỗi kết nối mạng, vui lòng thử lại". Lần bấm tiếp theo sẽ tự động resume (tải tiếp) nhờ cơ chế cache của Ollama.
- **Thiếu dung lượng ổ cứng:** Nếu API trả về lỗi không đủ không gian lưu trữ (Insufficient Storage), pop-up cảnh báo user dọn dẹp ổ đĩa (VD: "Cần tối thiểu 10GB dung lượng trống").
- **Hủy tải (Cancel):** Cung cấp nút (X) để người dùng có quyền ngắt luồng tải giữa chừng nếu muốn đổi sang model khác nhẹ hơn.