"""BOC AR manager prompt contract (parity with BEA/HSBC)."""
from app.bank_prompts.boc import BOC_AR_MANAGER_PROMPT_PREFIX


def test_boc_ar_manager_prompt_export():
    assert "BOC" in BOC_AR_MANAGER_PROMPT_PREFIX
    assert "BOOKKEEPER_DRAFT_JSON" in BOC_AR_MANAGER_PROMPT_PREFIX
    assert "原幣結餘" in BOC_AR_MANAGER_PROMPT_PREFIX
