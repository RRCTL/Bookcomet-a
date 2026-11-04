#!/usr/bin/env python
"""
Pre-download EasyOCR models to avoid timeout during first API call.
Run this once after installing EasyOCR.
"""
import easyocr

print("Downloading EasyOCR models (this may take a few minutes)...")
print("Languages: English (en), Chinese Simplified (ch_sim)")
print("")

try:
    reader = easyocr.Reader(['en', 'ch_sim'], gpu=False)
    print("[SUCCESS] Models downloaded successfully!")
    print("[SUCCESS] EasyOCR is ready to use")
    print("")
    print("If you want EasyOCR, set OCR_PROVIDER=easy in your .env file.")
    print("Default OCR provider is qwen-vl-ocr-latest.")
    print("Configure the VLM gateway URL in Settings → API (no vendor default).")
except Exception as e:
    print(f"[ERROR] Error downloading models: {e}")
    print("Try running this script again, or check your internet connection")
