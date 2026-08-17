# Project Binaries

This folder contains project-local binary dependencies that are not installed system-wide.

## Contents

### Poppler (PDF Processing)

- **Location:** `poppler/poppler-25.12.0/Library/bin/`
- **Purpose:** PDF to image conversion for OCR
- **Version:** 25.12.0
- **License:** GPL

The backend startup script (`backend/start_backend.ps1`) automatically adds this to PATH.

### Why Local Installation?

1. **Portability** - Project works on any machine without system-wide installation
2. **Version Control** - Ensures consistent Poppler version across all environments
3. **No Admin Rights** - Can be deployed without administrator privileges
4. **Isolation** - Doesn't conflict with other projects or system tools

## Installation

Poppler is automatically downloaded when you run the setup. If you need to reinstall:

```powershell
# From project root
$url = "https://github.com/oschwartz10612/poppler-windows/releases/download/v25.12.0-0/Release-25.12.0-0.zip"
Invoke-WebRequest -Uri $url -OutFile "$env:TEMP\poppler.zip"
Expand-Archive -Path "$env:TEMP\poppler.zip" -DestinationPath ".\bin\poppler" -Force
```

## Verification

```powershell
# Test Poppler
& ".\bin\poppler\poppler-25.12.0\Library\bin\pdfinfo.exe" -v
```

Expected output:
```
pdfinfo version 25.12.0
Copyright 2005-2025 The Poppler Developers
```
