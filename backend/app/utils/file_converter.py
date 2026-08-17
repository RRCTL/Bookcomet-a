# File Converter Utilities - Convert PDF to images using PyMuPDF
import io
import logging
import math
import os
from PIL import Image
from typing import List, Dict, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


def _pil_open_pixel_budget() -> int:
    """Max width*height Pillow allows before DecompressionBombError (pixels > 2 * MAX_IMAGE_PIXELS)."""
    raw = os.getenv("PDF_RENDER_MAX_PIXELS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            logger.warning("Invalid PDF_RENDER_MAX_PIXELS=%s; using default from PIL", raw)
    cap = Image.MAX_IMAGE_PIXELS
    if cap is None:
        return 178_956_970
    return max(1, int(2 * cap * 0.97))


def _page_pixmap_within_budget(page, base_zoom: float, page_number: int) -> Tuple[object, float]:
    """Rasterize one PDF page so PIL can open the PNG without DecompressionBombError."""
    import fitz  # PyMuPDF

    budget = _pil_open_pixel_budget()
    w_pt = max(float(page.rect.width), 1e-9)
    h_pt = max(float(page.rect.height), 1e-9)
    z = min(float(base_zoom), math.sqrt(budget / (w_pt * h_pt)))

    for attempt in range(24):
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z))
        px = pix.width * pix.height
        if px <= budget:
            if abs(z - base_zoom) > 1e-6 or attempt > 0:
                logger.info(
                    "PDF page %s: render zoom=%.4f (requested %.4f), pixmap %sx%s (%s px, budget %s)",
                    page_number,
                    z,
                    base_zoom,
                    pix.width,
                    pix.height,
                    px,
                    budget,
                )
            return pix, z
        z_new = z * math.sqrt(budget / px) * 0.97
        if z_new >= z * 0.999:
            z_new = z * 0.5
        z = z_new
        if z < 1e-6:
            raise Exception(
                f"PDF page {page_number} is too large to rasterize within pixel limit {budget}. "
                "Re-export the PDF at a smaller page size, lower PDF_RENDER_ZOOM, or raise "
                "PDF_RENDER_MAX_PIXELS if you accept higher memory use."
            )

    raise Exception(
        f"PDF page {page_number}: could not rasterize within pixel budget {budget}. "
        "Try lowering PDF_RENDER_ZOOM."
    )


def convert_pdf_to_images_list(pdf_path: str, target_format: str = 'PNG') -> List[Dict[str, Any]]:
    """
    Convert all pages of PDF to images (one image per page)
    
    Args:
        pdf_path: Path to PDF file
        target_format: Target image format ('JPEG' or 'PNG'), defaults to 'PNG'
    
    Returns:
        List of dictionaries, each containing:
        - 'image_path': Path to saved image file
        - 'page_number': Page number (1-indexed)
        - 'total_pages': Total number of pages in PDF
    
    Raises:
        Exception: If PDF conversion fails
    """
    try:
        import fitz  # PyMuPDF
        
        # Open PDF file
        pdf_document = fitz.open(pdf_path)
        
        if pdf_document.page_count == 0:
            pdf_document.close()
            raise Exception("PDF file has no pages")
        
        logger.info(f"PDF has {pdf_document.page_count} page(s)")
        
        images_list = []
        base_filename = Path(pdf_path).stem
        file_ext = 'png' if target_format.upper() == 'PNG' else 'jpg'
        
        # Convert each page to image
        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            
            # Render page to image (pixmap)
            # Use configurable zoom for better OCR quality.
            # Default 4.17 → 72 DPI × 4.17 ≈ 300 DPI (recommended for small thermal-print text).
            # Zoom is reduced automatically on large pages so PIL does not hit DecompressionBombError.
            base_zoom = float(os.getenv("PDF_RENDER_ZOOM", "4.17"))
            pix, _used_zoom = _page_pixmap_within_budget(page, base_zoom, page_num + 1)
            
            # Convert pixmap to PIL Image
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            # Convert to RGB mode (required for JPEG)
            if target_format.upper() == 'JPEG':
                if img.mode != 'RGB':
                    img = img.convert('RGB')
            else:
                # For PNG, preserve transparency if present
                if img.mode not in ['RGB', 'RGBA']:
                    img = img.convert('RGBA')
            
            # Save to temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'_page{page_num+1}.{file_ext}', mode='wb') as tmp_img:
                if target_format.upper() == 'PNG':
                    img.save(tmp_img, format='PNG', optimize=True)
                else:
                    img.save(tmp_img, format='JPEG', quality=95, optimize=True)
                
                image_path = tmp_img.name
                logger.info(f"Saved page {page_num + 1} to {image_path}")
            
            images_list.append({
                'image_path': image_path,
                'page_number': page_num + 1,
                'total_pages': pdf_document.page_count
            })
        
        pdf_document.close()
        logger.info(f"Successfully converted {len(images_list)} page(s) from PDF")
        return images_list
    
    except ImportError:
        raise Exception(
            "PyMuPDF (fitz) library is not installed. "
            "Please install it: pip install PyMuPDF"
        )
    except Exception as e:
        logger.error(f"PDF to images conversion failed: {str(e)}", exc_info=True)
        raise Exception(f"PDF to images conversion failed: {str(e)}")


def pdf_document_page_count(pdf_path: str) -> int:
    """Return PDF page count; raises if unreadable."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise Exception("PyMuPDF (fitz) library is not installed.") from None
    doc = fitz.open(pdf_path)
    try:
        n = doc.page_count
        if n <= 0:
            raise Exception("PDF file has no pages")
        return n
    finally:
        doc.close()


def convert_one_pdf_page_to_temp_png(pdf_path: str, page_number_one_based: int) -> str:
    """
    Rasterize a single PDF page to a temp PNG. Caller must unlink the path when done.
    page_number_one_based: 1 .. page_count
    """
    try:
        import fitz  # PyMuPDF
        import tempfile
    except ImportError:
        raise Exception(
            "PyMuPDF (fitz) library is not installed. "
            "Please install it: pip install PyMuPDF"
        ) from None

    if page_number_one_based < 1:
        raise ValueError("page_number_one_based must be >= 1")
    pdf_document = fitz.open(pdf_path)
    try:
        n = pdf_document.page_count
        if n == 0:
            pdf_document.close()
            raise Exception("PDF file has no pages")
        if page_number_one_based > n:
            raise Exception(
                f"Page {page_number_one_based} out of range (PDF has {n} page(s))"
            )
        page_idx = page_number_one_based - 1
        page = pdf_document[page_idx]
        base_zoom = float(os.getenv("PDF_RENDER_ZOOM", "4.17"))
        pix, _used_zoom = _page_pixmap_within_budget(page, base_zoom, page_number_one_based)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        if img.mode not in ["RGB", "RGBA"]:
            img = img.convert("RGBA")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_page{page_number_one_based}.png")
        tmp.close()
        img.save(tmp.name, format="PNG", optimize=True)
        logger.info("Saved PDF page %s to %s", page_number_one_based, tmp.name)
        return tmp.name
    finally:
        pdf_document.close()
