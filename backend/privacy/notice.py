"""
The consent notice (DPDP Act 2023 s.5, DPDP Rules 2025 r.3): for each purpose, the personal data items asked for,
what they are used for, how to withdraw, and how to complain. English and Hindi, side by side.

Any change to the text or the data items is a new NOTICE_VERSION; a consent is recorded against the version shown,
and consent given under an older version is not silently carried to a new one.
"""
from typing import Dict, List

from govcareeros.backend.config.settings import settings

NOTICE_VERSION = "2026-10-06.1"

ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
ALERTS = "ALERTS"
RESUME = "RESUME"
OPERATOR_ASSISTED_APPLICATION = "OPERATOR_ASSISTED_APPLICATION"

# Data item key -> (English label, Hindi label)
DATA_ITEMS: Dict[str, tuple] = {
    "date_of_birth": ("Date of birth", "जन्म तिथि"),
    "gender": ("Gender", "लिंग"),
    "category": ("Category / caste (UR, EWS, OBC, SC, ST)", "श्रेणी / जाति (अनारक्षित, ईडब्ल्यूएस, ओबीसी, एससी, एसटी)"),
    "disability": ("Disability status, percentage and type (PwBD)", "दिव्यांगता की स्थिति, प्रतिशत और प्रकार (PwBD)"),
    "citizenship_status": ("Citizenship / nationality", "नागरिकता / राष्ट्रीयता"),
    "ex_serviceman": ("Ex-serviceman status and armed forces service", "भूतपूर्व सैनिक स्थिति और सशस्त्र बल सेवा"),
    "service_details": ("Government service details (employer, pay level, dates, deputation)",
                        "सरकारी सेवा का विवरण (नियोक्ता, वेतन स्तर, तिथियाँ, प्रतिनियुक्ति)"),
    "certificates": ("Reservation / benefit certificates (kind, issue date, validity, issuing authority)",
                     "आरक्षण / लाभ प्रमाण-पत्र (प्रकार, जारी करने की तिथि, वैधता, जारी करने वाला प्राधिकारी)"),
    "education": ("Education (degrees, institutions, dates, marks)", "शिक्षा (डिग्री, संस्थान, तिथियाँ, अंक)"),
    "experience": ("Work experience", "कार्य अनुभव"),
    "email": ("Email address", "ईमेल पता"),
    "career_interests": ("Career interests and preferred locations", "करियर रुचियाँ और पसंदीदा स्थान"),
    "name_and_contact": ("Name, phone/contact and address", "नाम, फ़ोन/संपर्क और पता"),
    "skills_projects": ("Skills, projects, certifications, languages and achievements",
                        "कौशल, परियोजनाएँ, प्रमाणन, भाषाएँ और उपलब्धियाँ"),
    "documents": ("Documents you upload", "आपके द्वारा अपलोड किए गए दस्तावेज़"),
}

PURPOSES: Dict[str, dict] = {
    ELIGIBILITY_CHECK: {
        "data_items": ["date_of_birth", "gender", "category", "disability", "citizenship_status", "ex_serviceman",
                       "service_details", "certificates", "education", "experience"],
        "en": ("To check, with fixed rules (not AI), whether you meet the age, education, category, disability and "
               "service conditions written in official recruitment notifications, and to show each result with the "
               "notification page it comes from. The result is guidance only; the official notification prevails."),
        "hi": ("आधिकारिक भर्ती अधिसूचनाओं में लिखी आयु, शिक्षा, श्रेणी, दिव्यांगता और सेवा की शर्तों को आप पूरा करते हैं या नहीं, "
               "यह निश्चित नियमों (एआई नहीं) से जाँचने के लिए, और हर परिणाम को अधिसूचना के उस पृष्ठ के साथ दिखाने के लिए जहाँ से वह लिया गया है। "
               "परिणाम केवल मार्गदर्शन है; आधिकारिक अधिसूचना ही मान्य होगी।"),
    },
    ALERTS: {
        "data_items": ["email", "career_interests"],
        "en": "To send you alerts and digests about new vacancies, changed notices and closing dates.",
        "hi": "नई रिक्तियों, बदली हुई सूचनाओं और अंतिम तिथियों के बारे में आपको अलर्ट और सारांश भेजने के लिए।",
    },
    RESUME: {
        "data_items": ["name_and_contact", "education", "experience", "skills_projects"],
        "en": ("To build resume drafts that quote only what is in your profile; a draft is never sent anywhere "
               "without your approval."),
        "hi": "ऐसे बायोडाटा मसौदे बनाने के लिए जिनमें केवल आपकी प्रोफ़ाइल की जानकारी हो; आपकी स्वीकृति के बिना कोई मसौदा कहीं नहीं भेजा जाता।",
    },
    OPERATOR_ASSISTED_APPLICATION: {
        "data_items": ["name_and_contact", "date_of_birth", "gender", "category", "disability", "certificates",
                       "education", "experience", "documents"],
        "en": ("So that an operator, with you present, can help you prepare and fill an application on the official "
               "recruitment website. The operator records your consent; you check and approve before anything is submitted."),
        "hi": ("ताकि कोई ऑपरेटर, आपकी उपस्थिति में, आधिकारिक भर्ती वेबसाइट पर आवेदन तैयार करने और भरने में आपकी मदद कर सके। "
               "ऑपरेटर आपकी सहमति दर्ज करता है; कुछ भी जमा करने से पहले आप उसे जाँचते और स्वीकृत करते हैं।"),
    },
}

WITHDRAW = {
    "en": ("You can withdraw consent for any purpose at any time on the Privacy page (or DELETE /privacy/consents/{purpose}); "
           "it is as easy as giving it. After withdrawal we stop using that data for that purpose. Records of what was "
           "done before withdrawal are kept, not silently deleted; to delete your data, use \"Erase my account\". "
           "You can also download all your data (Export) and correct it by editing your profile."),
    "hi": ("आप किसी भी उद्देश्य के लिए दी गई सहमति कभी भी गोपनीयता पृष्ठ पर (या DELETE /privacy/consents/{purpose} से) वापस ले सकते हैं; "
           "यह सहमति देने जितना ही आसान है। वापस लेने के बाद हम उस उद्देश्य के लिए उस जानकारी का उपयोग बंद कर देते हैं। "
           "वापसी से पहले किए गए कार्यों के रिकॉर्ड रखे जाते हैं, चुपचाप मिटाए नहीं जाते; अपनी जानकारी मिटाने के लिए \"मेरा खाता मिटाएँ\" का उपयोग करें। "
           "आप अपनी पूरी जानकारी डाउनलोड (Export) भी कर सकते हैं और प्रोफ़ाइल संपादित करके उसे सुधार सकते हैं।"),
}

MINORS = {
    "en": "If you are under 18, a parent or lawful guardian must give this consent; enter their name.",
    "hi": "यदि आपकी आयु 18 वर्ष से कम है, तो यह सहमति माता-पिता या वैध अभिभावक को देनी होगी; उनका नाम दर्ज करें।",
}

GRIEVANCE_NOT_CONFIGURED = "Grievance officer contact not yet configured (set PRIVACY_GRIEVANCE_CONTACT)"

def grievance() -> Dict[str, str]:
    contact = settings.PRIVACY_GRIEVANCE_CONTACT or GRIEVANCE_NOT_CONFIGURED
    return {
        "contact": contact,
        "en": (f"Questions or complaints: {contact}. If you are not satisfied with our reply you may complain to the "
               "Data Protection Board of India."),
        "hi": f"प्रश्न या शिकायत: {contact}। हमारे उत्तर से संतुष्ट न होने पर आप भारतीय डेटा संरक्षण बोर्ड से शिकायत कर सकते हैं।",
    }

def data_items(purpose: str) -> List[str]:
    return list(PURPOSES[purpose]["data_items"])

def notice() -> dict:
    return {
        "version": NOTICE_VERSION,
        "purposes": [
            {"purpose": key, "en": p["en"], "hi": p["hi"],
             "data_items": [{"key": k, "en": DATA_ITEMS[k][0], "hi": DATA_ITEMS[k][1]} for k in p["data_items"]]}
            for key, p in PURPOSES.items()
        ],
        "withdraw": WITHDRAW,
        "minors": MINORS,
        "grievance": grievance(),
    }
