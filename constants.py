"""Static configuration shared across route modules: nav structure, supported
languages, onboarding questions/options and the fixed BIS checklist/portal
lists. Kept separate from `server.py` and `routes/` so any module can import
these without risking a circular import on the Flask `app` object."""

# Top navigation shown to a logged-in user with an active product workspace.
# (endpoint, label) — labels are overridden per-language in inject_globals().
NAV_LINKS = [
    ('home',         'Home'),
    ('standards',    'Standards'),
    ('schemes',      'Schemes'),
    ('testing_labs', 'Testing & Labs'),
    ('licensing',    'Licensing'),
    ('documents',    'Documents'),
    ('checklist',    'Checklist'),
    ('my_cases',     'My Cases'),
    ('photo_check',  'Photo Check'),
]

SUPPORTED_LANGS = {'en': 'English', 'hi': 'हिंदी', 'te': 'తెలుగు'}

# Endpoints a logged-in user may hit before finishing onboarding.
ONBOARDING_EXEMPT = {
    'onboarding', 'logout', 'static', 'set_language', 'index',
    'my_cases', 'case_detail', 'activate_case', 'get_case_pdf', 'google_auth',
}

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi (NCT)",
    "Jammu & Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Andaman & Nicobar Islands", "Dadra & Nagar Haveli and Daman & Diu", "Lakshadweep",
]
USER_TYPES = ["Manufacturer", "Importer", "Trader / Distributor", "Startup / MSME",
              "Compliance consultant", "Student", "Consumer"]

ONB_QUESTIONS = [
    {"key": "user_type", "prompt": "To set up your workspace, what best describes you?",
     "type": "choice", "options": USER_TYPES},
    {"key": "product", "prompt": "Which product are you working with?",
     "type": "product"},
    {"key": "location", "prompt": "Where are you located?", "type": "location"},
]

LICENSING_PORTALS = [
    {"key": "manakonline", "name": "BIS Manak Online",
     "desc": "Apply for a Scheme I (ISI Mark) licence, track applications, pay fees.",
     "url": "https://www.manakonline.in/MANAK/login"},
    {"key": "crsbis", "name": "BIS CRS Portal",
     "desc": "Register a model under the Compulsory Registration Scheme (Scheme II).",
     "url": "https://www.crsbis.in/BIS/registration-page.do"},
    {"key": "bis_overview", "name": "BIS Product Certification",
     "desc": "Official process overview, Scheme I guidelines and fee schedule.",
     "url": "https://www.bis.gov.in/product-certification/product-certification-overview/?lang=en"},
    {"key": "lims", "name": "BIS Recognised Labs (LIMS)",
     "desc": "Find a BIS-recognised laboratory in your product's scope.",
     "url": "https://lims.bis.gov.in/home/labs/"},
    {"key": "care", "name": "BIS Care",
     "desc": "Verify a licence / registration number and file complaints.",
     "url": "https://www.bis.gov.in/"},
]

# The 7-row Checklist page (label, area key, endpoint to view it).
CHECKLIST_ROWS = [
    ("Applicable Standard",       "standards",     "standards"),
    ("Certification Requirement", "certification", "schemes"),
    ("BIS Scheme",                "scheme",        "schemes"),
    ("Testing Requirement",       "testing",       "testing_labs"),
    ("Recognised Laboratory",     "labs",          "testing_labs"),
    ("Required Documents",        "documents",     "documents"),
    ("Licensing Process",         "licensing",     "licensing"),
]
