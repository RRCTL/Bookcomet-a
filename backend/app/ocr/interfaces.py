from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float
    bbox: List[int]


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    bbox: List[int]
    words: List[OcrWord]


@dataclass(frozen=True)
class OcrResult:
    text: str
    lines: List[OcrLine]
    metadata: Dict[str, str]


class OcrProvider:
    name: str

    async def recognize(
        self,
        image_path: str,
        *,
        prompt_override: str | None = None,
        ocr_options: Dict | None = None,
        image_options: Dict | None = None,
    ) -> OcrResult:
        raise NotImplementedError

