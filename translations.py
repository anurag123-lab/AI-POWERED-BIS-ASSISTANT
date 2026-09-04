"""
Static UI-chrome translations (EN / HI / TE).

Covers navigation labels, page headings and common buttons - the fixed text
of the interface. Dynamic answer bodies are translated at answer time by
services.llm.translate when an OpenAI key with credit is available; without
it they stay in English (IS numbers and citations always stay verbatim).
"""

STRINGS = {
    "en": {
        "nav.home": "Home",
        "nav.standards": "Standards",
        "nav.schemes": "Schemes",
        "nav.testing_labs": "Testing & Labs",
        "nav.licensing": "Licensing",
        "nav.documents": "Documents",
        "nav.checklist": "Checklist",
        "nav.my_cases": "My Cases",
        "nav.photo_check": "Photo Check",
        "common.your_workspace": "Your BIS Workspace",
        "common.ask_assistant": "AI Assistant",
        "common.view_source": "View Source",
        "common.mark_reviewed": "Mark reviewed",
        "common.reviewed": "Reviewed",
        "common.official_portals": "Official portals",
        "common.generate_pdf": "Generate complete PDF",
        "common.history": "History",
        "pdf.title": "BIS Compliance Report",
    },
    "hi": {
        "nav.home": "होम",
        "nav.standards": "मानक",
        "nav.schemes": "स्कीम",
        "nav.testing_labs": "परीक्षण एवं प्रयोगशालाएँ",
        "nav.licensing": "लाइसेंसिंग",
        "nav.documents": "दस्तावेज़",
        "nav.checklist": "चेकलिस्ट",
        "nav.my_cases": "मेरे केस",
        "nav.photo_check": "फोटो जाँच",
        "common.your_workspace": "आपका BIS कार्यक्षेत्र",
        "common.ask_assistant": "AI सहायक",
        "common.view_source": "स्रोत देखें",
        "common.mark_reviewed": "समीक्षित चिह्नित करें",
        "common.reviewed": "समीक्षित",
        "common.official_portals": "आधिकारिक पोर्टल",
        "common.generate_pdf": "पूर्ण PDF बनाएँ",
        "common.history": "इतिहास",
        "pdf.title": "BIS अनुपालन रिपोर्ट",
    },
    "te": {
        "nav.home": "హోమ్",
        "nav.standards": "ప్రమాణాలు",
        "nav.schemes": "స్కీమ్‌లు",
        "nav.testing_labs": "పరీక్ష & ల్యాబ్‌లు",
        "nav.licensing": "లైసెన్సింగ్",
        "nav.documents": "పత్రాలు",
        "nav.checklist": "చెక్‌లిస్ట్",
        "nav.my_cases": "నా కేసులు",
        "nav.photo_check": "ఫోటో తనిఖీ",
        "common.your_workspace": "మీ BIS వర్క్‌స్పేస్",
        "common.ask_assistant": "AI అసిస్టెంట్",
        "common.view_source": "మూలం చూడండి",
        "common.mark_reviewed": "సమీక్షించినట్లు గుర్తించండి",
        "common.reviewed": "సమీక్షించబడింది",
        "common.official_portals": "అధికారిక పోర్టల్‌లు",
        "common.generate_pdf": "పూర్తి PDF రూపొందించండి",
        "common.history": "చరిత్ర",
        "pdf.title": "BIS సమ్మతి నివేదిక",
    },
}


def t(key, lang="en"):
    lang = (lang or "en").lower()
    return STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key) or key
