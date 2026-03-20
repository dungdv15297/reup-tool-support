import asyncio
import os
from tts_service import process_text_only, process_srt_logic
from srt_utils import parse_srt

# Sample SRT content
SRT_CONTENT = """1
00:00:00,000 --> 00:00:02,000
Chào bạn, đây là mẫu.

2
00:00:03,000 --> 00:00:05,000
Dòng này có <b>thẻ HTML</b>.

3
00:00:07,000 --> 00:00:10,000
Kết thúc thử nghiệm."""

async def test_tts():
    voice = "vi-VN-HoaiMyNeural"
    
    # Test Text Only
    text = "Chào mừng bạn đến với công cụ hỗ trợ reup."
    print("Testing text processing...")
    txt_path = await process_text_only(text, voice)
    if os.path.exists(txt_path):
        print(f"Text processing OK: {txt_path}")
        os.remove(txt_path)
    
    # Test SRT Processing
    print("Testing SRT processing with callback...")
    subs = parse_srt(SRT_CONTENT)
    
    def test_cb(p, t):
        print(f"Progress: {p*100:.1f}% - {t}")
        
    srt_path = await process_srt_logic(subs, voice, test_cb)
    if os.path.exists(srt_path):
        print(f"SRT processing OK: {srt_path}")
        os.remove(srt_path)

if __name__ == "__main__":
    asyncio.run(test_tts())
