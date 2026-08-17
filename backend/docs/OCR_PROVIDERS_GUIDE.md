# OCR Providers Comparison Guide

This document explains how to install, configure, and compare different OCR providers.

## Available OCR Providers

### 1. **DeepSeek OCR (DMXAPI)** ✅ (Cloud OCR)
- **Status**: Supported via OpenAI-compatible endpoint
- **Pros**: Strong accuracy, no local OCR install, good for mixed Chinese/English
- **Cons**: Requires API key, network latency, cost per request
- **Best for**: Production OCR with consistent quality

### 2. **EasyOCR** ✅ (Local OCR)
- **Status**: Installed and working
- **Pros**: Easy to install, supports 80+ languages, good for general use
- **Cons**: Slower, less accurate for handwritten text
- **Best for**: Printed documents, multi-language text

### 3. **Tesseract** 🔨 (Local OCR, Requires System Installation)
- **Status**: Python package installed, binary needed
- **Pros**: Fast, widely used, excellent for printed text, free
- **Cons**: Requires system installation, less accurate for handwritten text
- **Best for**: Scanned documents, printed text, forms

---

## Installation Instructions

### DeepSeek OCR (DMXAPI)

Set in `backend/.env`:
```env
OCR_PROVIDER=DeepSeek-OCR
DEEPSEEK_OCR_API_KEY=sk-xxx
DEEPSEEK_OCR_BASE_URL=https://www.dmxapi.cn
DEEPSEEK_OCR_MODEL=DeepSeek-OCR
DEEPSEEK_OCR_PROMPT=<image>\nFree OCR.
```

---

### EasyOCR (✅ Already Installed)

Already working! Just set in `backend/.env`:
```env
OCR_PROVIDER=easy
```

---

### Tesseract OCR (Recommended for Speed & Accuracy)

#### Step 1: Install Tesseract Binary

**Download and install:** https://github.com/UB-Mannheim/tesseract/wiki

**Direct download link (Windows 64-bit):**
https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.3.3.20231005.exe

**Installation notes:**
- Use default installation path: `C:\Program Files\Tesseract-OCR\`
- ✅ Check "Add to PATH" during installation
- Install language packs: English + Chinese Simplified

#### Step 2: Verify Installation

Open new CMD window and run:
```cmd
tesseract --version
```

Should show: `tesseract 5.x.x`

#### Step 3: Configure Provider

Edit `backend/.env`:
```env
OCR_PROVIDER=tesseract
```

#### Step 4: Test

Upload an image at http://localhost:5173

---

## Quick Comparison Test

### 1. Test DeepSeek OCR (Cloud)
```env
OCR_PROVIDER=DeepSeek-OCR
DEEPSEEK_OCR_BASE_URL=https://www.dmxapi.cn/v1
```
Upload image → Note accuracy and speed

### 2. Test EasyOCR (Local)
```env
OCR_PROVIDER=easy
```
Upload image → Note accuracy and speed

### 3. Test Tesseract (After Installation)
```env
OCR_PROVIDER=tesseract
```
Upload same image → Compare results

---

## Performance Comparison Table

| Provider | Speed | Accuracy (Printed) | Accuracy (Handwritten) | Chinese Support | Installation |
|----------|-------|-------------------|------------------------|-----------------|--------------|
| **DeepSeek OCR** | ⚡⚡ Medium | ⭐⭐⭐⭐ Excellent | ⭐⭐⭐ Good | ⭐⭐⭐⭐ Excellent | 🟢 Easy |
| **Tesseract** | ⚡⚡⚡ Fast | ⭐⭐⭐⭐ Excellent | ⭐⭐ Fair | ⭐⭐⭐ Good | 🟡 Moderate |
| **EasyOCR** | ⚡⚡ Medium | ⭐⭐⭐ Good | ⭐⭐⭐ Good | ⭐⭐⭐⭐ Excellent | 🟢 Easy |

---

## Recommendations

### For Production OCR (Best Accuracy)
**Use DeepSeek OCR** - Consistent quality without local OCR setup

### For English Cheques & Forms (Local)
**Use Tesseract** - Fastest and most accurate for printed English text

### For Chinese + English Mixed Documents (Local)
**Use EasyOCR** - Easy to install, good multilingual support

### For Production (Best Results)
Consider using an **ensemble approach**:
1. Run multiple OCR engines in parallel
2. Compare results and confidence scores
3. Choose the best result or combine them

---

## Troubleshooting

### Tesseract: "tesseract command not found"
- Restart terminal after installation
- Manually add to PATH: `C:\Program Files\Tesseract-OCR\`
- Or set in Python: `pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'`

### DeepSeek OCR: API errors
- Check `DEEPSEEK_OCR_API_KEY`
- Verify `DEEPSEEK_OCR_BASE_URL=https://www.dmxapi.cn/v1`
- Retry with a smaller image or lower resolution

### EasyOCR: Slow first run
- Normal! Downloads models (~100MB) on first use
- Subsequent runs are faster

---

## Next Steps

1. **Install Tesseract** (recommended first step)
2. **Test all providers** with the same image
3. **Choose the best** for your use case
4. **Update `.env`** with your preferred provider

