from typing import Dict, List, Optional
import re
from dataclasses import dataclass

from app.ocr.interfaces import OcrResult, OcrLine


@dataclass
class FieldIdentificationResult:
    """Field identification result"""
    field_name: str
    value: Optional[str]
    confidence: float
    method: str  # "regex", "keyword", "bbox_region"
    raw_text: str
    validation_passed: bool
    error_message: Optional[str] = None


class BaseFieldIdentifier:
    """Base field identifier with regex, keyword, and bbox filtering"""
    
    def __init__(self, field_name: str):
        self.field_name = field_name
        self.patterns: List[Dict] = []
        self.keywords: List[Dict] = []
        self.validators: List = []
        self.cleaners: List = []
        self.region: Optional[Dict[str, tuple]] = None
    
    def set_region(self, x_range: tuple, y_range: tuple):
        """Set bbox region for this field"""
        self.region = {"x": x_range, "y": y_range}
        return self
    
    def add_pattern(self, pattern: str, confidence: float = 0.95):
        """Add regex pattern"""
        self.patterns.append({
            "pattern": re.compile(pattern, re.IGNORECASE),
            "confidence": confidence
        })
        return self
    
    def add_keyword(self, keyword: str, confidence: float = 0.80):
        """Add keyword"""
        self.keywords.append({
            "keyword": keyword.lower(),
            "confidence": confidence
        })
        return self
    
    def add_validator(self, validator_func):
        """Add validator function"""
        self.validators.append(validator_func)
        return self
    
    def add_cleaner(self, cleaner_func):
        """Add cleaner function"""
        self.cleaners.append(cleaner_func)
        return self
    
    def identify(self, ocr_result: OcrResult) -> FieldIdentificationResult:
        """Identify field from OCR result"""
        # Filter lines by region if set
        lines = self._filter_lines_by_region(ocr_result.lines)
        
        # Try pattern matching
        result = self._try_pattern_matching(lines)
        if result:
            return result
        
        # Try keyword matching
        result = self._try_keyword_matching(lines)
        if result:
            return result
        
        # Return failure
        return FieldIdentificationResult(
            field_name=self.field_name,
            value=None,
            confidence=0.0,
            method="none",
            raw_text="",
            validation_passed=False,
            error_message=f"Unable to identify field: {self.field_name}"
        )
    
    def _filter_lines_by_region(self, lines: List[OcrLine]) -> List[OcrLine]:
        """Filter lines by bbox region"""
        if not self.region:
            return lines
        
        filtered = []
        for line in lines:
            if self._is_in_region(line.bbox):
                filtered.append(line)
        return filtered
    
    def _is_in_region(self, bbox: List[int]) -> bool:
        """Check if bbox is in region"""
        if not self.region or len(bbox) < 4:
            return True
        
        # Handle both 4-coord [x1,y1,x2,y2] and 8-coord [x1,y1,x2,y2,x3,y3,x4,y4] formats
        if len(bbox) == 8:
            # EasyOCR format: 4 corner points
            x_coords = [bbox[0], bbox[2], bbox[4], bbox[6]]
            y_coords = [bbox[1], bbox[3], bbox[5], bbox[7]]
            x1, x2 = min(x_coords), max(x_coords)
            y1, y2 = min(y_coords), max(y_coords)
        else:
            # Standard format: top-left + bottom-right
            x1, y1, x2, y2 = bbox[:4]
        
        x_center = (x1 + x2) / 2
        y_center = (y1 + y2) / 2
        
        x_min, x_max = self.region["x"]
        y_min, y_max = self.region["y"]
        
        return x_min <= x_center <= x_max and y_min <= y_center <= y_max
    
    def _try_pattern_matching(self, lines: List[OcrLine]) -> Optional[FieldIdentificationResult]:
        """Try regex pattern matching"""
        text = " ".join([line.text for line in lines])
        
        for pattern_config in self.patterns:
            pattern = pattern_config["pattern"]
            confidence = pattern_config["confidence"]
            
            match = pattern.search(text)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
                value = self._clean_value(value)
                is_valid = self._validate_value(value)
                
                return FieldIdentificationResult(
                    field_name=self.field_name,
                    value=value if is_valid else None,
                    confidence=confidence if is_valid else confidence * 0.5,
                    method="regex",
                    raw_text=match.group(0),
                    validation_passed=is_valid
                )
        
        return None
    
    def _try_keyword_matching(self, lines: List[OcrLine]) -> Optional[FieldIdentificationResult]:
        """Try keyword matching"""
        text = " ".join([line.text for line in lines]).lower()
        
        for keyword_config in self.keywords:
            keyword = keyword_config["keyword"]
            confidence = keyword_config["confidence"]
            
            if keyword in text:
                idx = text.find(keyword)
                value = text[idx + len(keyword):].strip()
                value = self._clean_value(value)
                is_valid = self._validate_value(value)
                
                return FieldIdentificationResult(
                    field_name=self.field_name,
                    value=value if is_valid else None,
                    confidence=confidence if is_valid else confidence * 0.5,
                    method="keyword",
                    raw_text=keyword,
                    validation_passed=is_valid
                )
        
        return None
    
    def _clean_value(self, value: str) -> str:
        """Clean value"""
        for cleaner in self.cleaners:
            value = cleaner(value)
        return value
    
    def _validate_value(self, value: str) -> bool:
        """Validate value"""
        if not value:
            return False
        
        for validator in self.validators:
            if not validator(value):
                return False
        
        return True


class ChequeNumberIdentifier(BaseFieldIdentifier):
    """Cheque number identifier"""
    
    def __init__(self):
        super().__init__("cheque_number")
        self.add_pattern(r"CHQ\s*[#:]?\s*(\d{6,10})", 0.98)
        self.add_pattern(r"NO\.\s*(\d{6,10})", 0.95)
        self.add_pattern(r"CHECK\s*NO\.\s*(\d{6,10})", 0.95)
        self.add_cleaner(lambda x: x.strip())
        self.add_cleaner(lambda x: re.sub(r"[^\d]", "", x))
        self.add_validator(lambda x: len(x) >= 6)
        self.add_validator(lambda x: len(x) <= 10)
        self.add_validator(lambda x: x.isdigit())
        self.set_region((0.75, 0.98), (0.02, 0.08))


class AmountIdentifier(BaseFieldIdentifier):
    """Amount identifier"""
    
    def __init__(self):
        super().__init__("amount")
        self.add_pattern(r"[$¥£€]\s*(\d+[,.]?\d*)", 0.98)
        self.add_pattern(r"HK\s*\$\s*(\d+[,.]?\d*)", 0.97)
        self.add_cleaner(lambda x: x.strip())
        self.add_cleaner(lambda x: x.replace(",", ""))
        self.add_validator(self._is_valid_amount)
        self.set_region((0.70, 0.98), (0.32, 0.42))
    
    @staticmethod
    def _is_valid_amount(amount_str: str) -> bool:
        try:
            amount = float(amount_str.replace("$", "").replace(",", ""))
            return 0 < amount < 10000000
        except:
            return False


class FieldFilteringPipeline:
    """Field filtering pipeline with layout-aware filtering"""
    
    def __init__(self):
        self.identifiers = {
            "cheque_number": ChequeNumberIdentifier(),
            "amount": AmountIdentifier(),
        }
        self.required_fields = ["cheque_number", "amount"]
        self.confidence_thresholds = {
            "cheque_number": 0.85,
            "amount": 0.85,
        }
    
    def filter_and_extract(self, ocr_result: OcrResult) -> Dict:
        """Filter and extract fields from OCR result"""
        identified_fields = {}
        identification_results = {}
        
        for field_name, identifier in self.identifiers.items():
            result = identifier.identify(ocr_result)
            identification_results[field_name] = result
            
            threshold = self.confidence_thresholds.get(field_name, 0.70)
            if result.value and result.confidence >= threshold:
                identified_fields[field_name] = result.value
        
        missing_fields = [f for f in self.required_fields if f not in identified_fields]
        
        overall_confidence = self._calculate_overall_confidence(identification_results)
        
        return {
            "status": "success" if not missing_fields else "incomplete",
            "fields": identified_fields,
            "missing_fields": missing_fields,
            "overall_confidence": overall_confidence,
            "field_details": {k: vars(v) for k, v in identification_results.items()},
        }
    
    def _calculate_overall_confidence(self, results: Dict) -> float:
        """Calculate overall confidence"""
        confidences = []
        for field in self.required_fields:
            if field in results:
                result = results[field]
                if result.validation_passed:
                    confidences.append(result.confidence)
                else:
                    confidences.append(result.confidence * 0.5)
        
        if not confidences:
            return 0.0
        
        return sum(confidences) / len(confidences)
