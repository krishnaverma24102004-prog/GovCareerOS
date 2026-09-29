import re
from typing import Dict, Any, List, Tuple
from govcareeros.backend.network.models import EndpointCandidate

class APIClassifier:
    RECRUITMENT_KEYWORDS = {
        "recruitment", "vacancy", "vacancies", "jobs", "job", "career", 
        "careers", "notice", "notification", "advertisement", "employment", 
        "exam", "results", "admit-card", "application", "post", "opening"
    }
    
    FIELD_INDICATORS = {
        "post_name", "postName", "designation", "vacancy", "vacancies", 
        "qualification", "eligibility", "application_start", "application_end", 
        "last_date", "deadline", "notification", "advertisement_number", 
        "department", "organization"
    }

    def classify(self, url: str, content_type: str, body: str) -> Tuple[bool, float, List[str], List[str]]:
        reasons = []
        detected_fields = []
        confidence = 0.0
        
        url_lower = url.lower()
        
        # 1. URL keywords
        if any(kw in url_lower for kw in self.RECRUITMENT_KEYWORDS):
            reasons.append("URL contains recruitment keywords")
            confidence += 0.4
            
        # 2. Content type
        if "application/json" in content_type.lower():
            reasons.append("JSON response")
            confidence += 0.1 # Lowered further to prevent generic JSON being candidates
            
        # 3. Body analysis for fields
        body_lower = body.lower()
        for field in self.FIELD_INDICATORS:
            if field.lower() in body_lower:
                detected_fields.append(field)
                
        if detected_fields:
            reasons.append(f"Contains recruitment fields: {len(detected_fields)}")
            confidence += 0.5 * (1 - (1/len(detected_fields + [1]))) 
            
        confidence = min(confidence, 1.0)
        is_candidate = confidence > 0.7 # Increased threshold to 0.7
        
        return is_candidate, confidence, reasons, detected_fields
