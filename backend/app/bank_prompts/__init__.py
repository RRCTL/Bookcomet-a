"""
Bank Prompts Package
====================
Registry of bank-specific VLM prompts and detection keywords.

To add a new bank:
  1. Create backend/app/bank_prompts/<bank_id>.py with PROMPT and KEYWORDS.
  2. Add two lines here: import the module and register its entries below.
  No other file needs to change.
"""

from .bea   import PROMPT as _bea_prompt,   KEYWORDS as _bea_kw
from .boc    import PROMPT as _boc_prompt,   KEYWORDS as _boc_kw
from .bocom  import PROMPT as _bocom_prompt,  KEYWORDS as _bocom_kw
from .hang_seng import PROMPT as _hang_seng_prompt, KEYWORDS as _hang_seng_kw
from .hsbc   import PROMPT as _hsbc_prompt,  KEYWORDS as _hsbc_kw
from .ocbc   import PROMPT as _ocbc_prompt,  KEYWORDS as _ocbc_kw
from .sc     import PROMPT as _sc_prompt,    KEYWORDS as _sc_kw
from .default import PROMPT as _default_prompt

# Prompt lookup: bank_id → VLM prompt string
# Used by BankStatementParser._parse_with_ocr_fallback to select the correct prompt per track.
BANK_PROMPT_DATABASE: dict[str, str] = {
    "DEFAULT": _default_prompt,
    "BEA":     _bea_prompt,
    "BOC":     _boc_prompt,
    "BOCOM":   _bocom_prompt,
    "HANG_SENG": _hang_seng_prompt,
    "HSBC":    _hsbc_prompt,
    "OCBC":    _ocbc_prompt,
    "SCB":     _sc_prompt,
}

# Detection keywords: bank_id → list of text patterns
# Used by BankStatementParser.__init__ to populate self.bank_patterns for _detect_bank().
BANK_KEYWORDS: dict[str, list[str]] = {
    "BEA":   _bea_kw,
    "BOC":   _boc_kw,
    "BOCOM": _bocom_kw,
    "HANG_SENG": _hang_seng_kw,
    "HSBC":  _hsbc_kw,
    "OCBC":  _ocbc_kw,
    "SCB":   _sc_kw,
}
