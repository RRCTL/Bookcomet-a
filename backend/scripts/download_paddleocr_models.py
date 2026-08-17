#!/usr/bin/env python
"""
Pre-download PaddleOCR models to avoid timeout during first API call.
Run this once after installing PaddleOCR.
"""
from paddleocr import PaddleOCR

print("Downloading PaddleOCR models (this may take a few minutes)...")
print("Languages: Chinese (ch) - supports both Chinese + English")
print("")

try:
    # Initialize PaddleOCR - this will download models
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    print("[SUCCESS] Models downloaded successfully!")
    print("[SUCCESS] PaddleOCR is ready to use")
    print("")
    print("Note: PaddleOCR is deprecated in this project.")
    print("Recommended OCR_PROVIDER=qwen-vl-ocr-latest (or easy/tesseract if local OCR).")
    print("DMXAPI OCR base: https://www.dmxapi.cn")
except Exception as e:
    print(f"[ERROR] Error downloading models: {e}")
    print("Try running this script again, or check your internet connection")
