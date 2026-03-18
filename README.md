# 🎬 Auto Translator & TTS (Local AI)

Tool tự động hóa **phiên dịch** và **lồng tiếng** (Text-to-Speech) cho phụ đề video, chạy dưới dạng **ứng dụng desktop native** trên macOS. Sử dụng Local LLM qua **Ollama** và TTS miễn phí qua **edge-tts**.

---

## ✨ Tính năng

- 🖥️ **Ứng dụng desktop native** (cửa sổ riêng, không cần trình duyệt)
- 📁 Chọn file `.srt` / `.txt` qua dialog native của hệ điều hành
- 🧠 Chọn model AI local từ Ollama (ví dụ: `qwen2.5:14b`, `llama3`)
- 🌐 **Phiên dịch viên** — Dịch context-aware, văn phong GenZ, chunking thông minh
- 🎙️ **Thuyết minh viên** — 3 voice presets: Quảng cáo / Review Phim / Storytelling
- 💾 Tự động lưu settings (không phải chọn lại mỗi lần mở)
- 📋 Console real-time theo dõi tiến trình

---

## 🛠️ Cài đặt

### 1. Cài Ollama

```bash
# macOS
brew install ollama

# Hoặc tải từ: https://ollama.com/download
```

### 2. Pull model LLM

```bash
# Khuyến nghị dùng qwen2.5 (hỗ trợ tiếng Việt tốt)
ollama pull qwen2.5:14b

# Hoặc model nhỏ hơn
ollama pull qwen2.5:7b

# Hoặc llama3
ollama pull llama3
```

### 3. Chạy Ollama server

```bash
ollama serve
# Server chạy ở http://localhost:11434
```

### 4. Cài dependencies Python

```bash
# Tạo virtual environment (khuyến nghị)
python3 -m venv venv
source venv/bin/activate  # macOS/Linux

# Cài packages
pip install -r requirements.txt
```

> **Lưu ý:** `pydub` cần `ffmpeg` để xử lý audio:
> ```bash
> # macOS
> brew install ffmpeg
> ```

### 5. Chạy tool

```bash
python3 app.py
```

Ứng dụng sẽ mở một **cửa sổ desktop native** (không cần trình duyệt).

---

## 🚀 Cách sử dụng

1. **Chọn file** — Nhấn nút "Chọn file" để mở dialog chọn file `.srt` / `.txt`
2. **Chọn model** — Dropdown tự động load models từ Ollama
3. **Chọn thư mục output** — Nhấn nút "Chọn thư mục" để chọn nơi lưu kết quả
4. **Cài đặt dịch** — Chọn ngôn ngữ nguồn/đích (mặc định: Auto → Tiếng Việt)
5. **Nhấn "Bắt đầu Dịch"** — Theo dõi tiến trình trên Console
6. **Chọn voice preset** — 3 phong cách: Quảng cáo / Review Phim / Storytelling
7. **Nhấn "Bắt đầu TTS"** — Tạo file audio từ bản dịch

---

## 📂 Output

File được tự động đặt tên theo thời gian chạy:

| Loại | Tên file |
|------|----------|
| Bản dịch | `o_translate_ddmm_hhmmss.srt` (hoặc `.txt`) |
| Audio | `o_tts_ddmm_hhmmss.mp3` |

---

## 🎙️ Voice Presets

| Preset | Mô tả | Voice |
|--------|--------|-------|
| ⚡ Quảng cáo | Tươi sáng, dứt khoát, năng lượng cao | vi-VN-HoaiMyNeural |
| 🎬 Review Phim | Dồn dập, lôi cuốn, nhấn nhá twist | vi-VN-NamMinhNeural |
| 🌙 Storytelling | Trầm tĩnh, ma mị, hồi hộp | vi-VN-NamMinhNeural |

---

## 🏗️ Tech Stack

- **Desktop UI:** pywebview (native macOS window)
- **Backend:** Python Flask (embedded)
- **LLM:** Ollama (local)
- **TTS:** edge-tts (Microsoft Edge Neural Voices)
- **Audio:** pydub + ffmpeg
- **Frontend:** HTML / CSS / JavaScript (vanilla)
