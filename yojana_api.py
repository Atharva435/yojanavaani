from __future__ import annotations

import json
import hashlib
import os
import secrets
import threading
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC_CACHE: dict[str, tuple[bytes, str]] = {}
CATALOG_CACHE: dict[str, object] = {"expires_at": 0.0, "value": []}
CATALOG_CACHE_LOCK = threading.Lock()


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get(
    "YOJANAVAANI_DATABASE",
    str(Path(os.environ.get("YOJANAVAANI_DATA_DIR", ROOT / "data")) / "yojanavaani.sqlite3"),
))
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get(
    "HOST",
    "0.0.0.0" if os.environ.get("WEBSITE_INSTANCE_ID") else "127.0.0.1",
)
SECURE_COOKIES = os.environ.get(
    "YOJANAVAANI_SECURE_COOKIES",
    "1" if os.environ.get("WEBSITE_INSTANCE_ID") else "0",
).strip() == "1"
MAX_BODY_BYTES = 256_000
FIREBASE_CONFIG = {
    "apiKey": os.environ.get("FIREBASE_API_KEY", "").strip(),
    "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip(),
    "projectId": os.environ.get("FIREBASE_PROJECT_ID", "").strip(),
    "appId": os.environ.get("FIREBASE_APP_ID", "").strip(),
    "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", "").strip(),
}
FIREBASE_API_KEY = FIREBASE_CONFIG["apiKey"]
FIREBASE_AUTH_CONFIGURED = all(
    FIREBASE_CONFIG[key] for key in ("apiKey", "authDomain", "projectId", "appId")
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
LANGUAGE_NAMES = {
    "as": "Assamese", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "ks": "Kashmiri",
    "kok": "Konkani", "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri",
    "mr": "Marathi", "ne": "Nepali", "or": "Odia", "pa": "Punjabi",
    "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi", "ta": "Tamil",
    "te": "Telugu", "ur": "Urdu", "en": "English",
}
SESSION_COOKIE = "yv_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
SESSIONS: dict[str, dict] = {}
ASSISTANT_REQUESTS: dict[str, list[float]] = {}
ASSISTANT_REQUESTS_LOCK = threading.Lock()
ASSISTANT_REQUEST_LIMIT = 10
ASSISTANT_REQUEST_WINDOW = 60
STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
STATIC_FILES = {
    "/": ROOT / "index.html",
    "/index.html": ROOT / "index.html",
    "/styles.css": ROOT / "styles.css",
    "/locales.js": ROOT / "locales.js",
    "/app.js": ROOT / "app.js",
}
OFFICIAL_BENEFICIARY_STATS = {
    "pm-kisan": {
        "value": 94_465_646,
        "label": "farmers benefited in the Apr–Jul 2026–27 installment",
        "source_url": "https://pmkisan.gov.in/",
    },
}

SCHEMES = [
    {
        "id": "pm-kisan",
        "name": "PM-KISAN",
        "short_name": "PM-KISAN",
        "category": "Farmer support",
        "benefit": "Eligible landholding farmer families may receive ₹6,000 per year in three instalments.",
        "summary": "Income support for eligible landholding farmer families. Land records and exclusion rules are checked by the authorities.",
        "state": "All India",
        "official_url": "https://pmkisan.gov.in/",
        "apply_at": "PM-KISAN portal or a Common Service Centre (CSC)",
        "documents": ["Aadhaar card", "Bank account details", "Landholding records"],
        "steps": ["Check that your land record is available in your state's records.", "Apply or update details on the official PM-KISAN portal or at a CSC.", "Complete Aadhaar-based verification and check your application status on the portal."],
        "rules": {"required_occupation": "farmer", "requires_land": True},
        "note": "Landholding, family definition, e-KYC, and exclusion rules apply. A profile match is not an approval.",
        "hindi": {"name": "प्रधानमंत्री किसान सम्मान निधि", "short_name": "पीएम-किसान", "benefit": "पात्र भूमिधारक किसान परिवारों को तीन किस्तों में सालाना ₹6,000 मिल सकते हैं।", "summary": "पात्र भूमिधारक किसान परिवारों के लिए आय सहायता। अधिकारी भूमि रिकॉर्ड और अपवर्जन नियमों की जाँच करते हैं।"},
        "marathi": {"name": "प्रधानमंत्री किसान सन्मान निधी", "short_name": "पीएम-किसान", "benefit": "पात्र जमीनधारक शेतकरी कुटुंबांना तीन हप्त्यांत वर्षाला ₹6,000 मिळू शकतात।", "summary": "पात्र जमीनधारक शेतकरी कुटुंबांसाठी आर्थिक मदत. अधिकारी जमीन नोंदी आणि अपात्रतेचे नियम तपासतात."},
    },
    {
        "id": "pm-ujjwala",
        "name": "Pradhan Mantri Ujjwala Yojana",
        "short_name": "Ujjwala Yojana",
        "category": "Household support",
        "benefit": "Eligible adult women can apply for an LPG connection under the scheme.",
        "summary": "LPG connection support for eligible adult women from households without an existing LPG connection.",
        "state": "All India",
        "official_url": "https://www.pmuy.gov.in/",
        "apply_at": "A nearby LPG distributor or the official PMUY portal",
        "documents": ["Aadhaar card", "Ration card or family composition document", "Bank account details", "Address proof (if needed)"],
        "steps": ["Check the current eligibility conditions on the official PMUY portal.", "Submit the application and KYC documents to an LPG distributor.", "Keep the application receipt and follow up with the distributor."],
        "rules": {"required_gender": "woman", "requires_no_lpg": True},
        "note": "The administering agency checks household eligibility and the current scheme rules.",
        "hindi": {"name": "प्रधानमंत्री उज्ज्वला योजना", "short_name": "उज्ज्वला योजना", "benefit": "पात्र वयस्क महिलाएँ योजना के तहत LPG कनेक्शन के लिए आवेदन कर सकती हैं।", "summary": "ऐसे परिवारों की पात्र वयस्क महिलाओं के लिए LPG कनेक्शन सहायता जिनके पास पहले से LPG कनेक्शन नहीं है।"},
        "marathi": {"name": "प्रधानमंत्री उज्ज्वला योजना", "short_name": "उज्ज्वला योजना", "benefit": "पात्र प्रौढ महिला योजनेअंतर्गत LPG जोडणीसाठी अर्ज करू शकतात।", "summary": "आधीपासून LPG जोडणी नसलेल्या कुटुंबातील पात्र प्रौढ महिलांसाठी LPG जोडणी सहाय्य."},
    },
    {
        "id": "atal-pension-yojana",
        "name": "Atal Pension Yojana (APY)",
        "short_name": "Atal Pension Yojana",
        "category": "Pension",
        "benefit": "Subscribers can contribute toward a guaranteed pension of ₹1,000–₹5,000 per month from age 60.",
        "summary": "A voluntary pension scheme for people who join between ages 18 and 40 and have a savings bank or post office account.",
        "state": "All India",
        "official_url": "https://www.pfrda.org.in/",
        "apply_at": "Your bank or post office where you hold a savings account",
        "documents": ["Savings bank or post office account", "Aadhaar and mobile number (for KYC)"],
        "steps": ["Ask your bank or post office whether APY enrolment is open to you.", "Choose a pension amount and review the required contribution.", "Submit the enrolment form and keep the acknowledgement."],
        "rules": {"min_age": 18, "max_age": 40, "requires_bank_account": True},
        "note": "A person who is or has been an income-tax payer is not eligible to join APY from 1 October 2022. Confirm current rules with your bank.",
        "hindi": {"name": "अटल पेंशन योजना", "short_name": "अटल पेंशन योजना", "benefit": "अंशदाता 60 वर्ष की आयु से ₹1,000–₹5,000 मासिक पेंशन के लिए योगदान कर सकते हैं।", "summary": "18 से 40 वर्ष की आयु में जुड़ने वाले और बचत बैंक या डाकघर खाता रखने वाले लोगों के लिए स्वैच्छिक पेंशन योजना।"},
        "marathi": {"name": "अटल पेन्शन योजना", "short_name": "अटल पेन्शन योजना", "benefit": "सदस्य वयाच्या 60 वर्षांपासून दरमहा ₹1,000–₹5,000 पेन्शनसाठी योगदान देऊ शकतात।", "summary": "18 ते 40 वयोगटातील आणि बचत खाते असलेल्या लोकांसाठी ऐच्छिक पेन्शन योजना."},
    },
    {
        "id": "pm-jay",
        "name": "Ayushman Bharat PM-JAY",
        "short_name": "Ayushman Bharat",
        "category": "Health",
        "benefit": "Eligible families can receive cashless hospital cover of up to ₹5 lakh per family per year at empanelled hospitals.",
        "summary": "Hospital cover for families identified under PM-JAY. Eligibility is based on the official beneficiary database, not income alone.",
        "state": "All India",
        "official_url": "https://beneficiary.nha.gov.in/",
        "apply_at": "Check the official beneficiary portal or visit an Ayushman Mitra at an empanelled hospital",
        "documents": ["Aadhaar card or another accepted identity document", "Ration card or family ID (if available)"],
        "steps": ["Search for your family on the official PM-JAY beneficiary portal.", "If listed, complete identity verification at an authorised help desk.", "Use the card at an empanelled hospital and confirm covered treatment before care."],
        "rules": {"manual_verification": True},
        "note": "This checker cannot determine PM-JAY beneficiary status. Always check the official list or call 14555.",
        "hindi": {"name": "आयुष्मान भारत प्रधानमंत्री जन आरोग्य योजना", "short_name": "आयुष्मान भारत", "benefit": "पात्र परिवारों को सूचीबद्ध अस्पतालों में प्रति परिवार प्रति वर्ष ₹5 लाख तक का कैशलेस इलाज मिल सकता है।", "summary": "PM-JAY की आधिकारिक लाभार्थी सूची में पहचाने गए परिवारों के लिए अस्पताल कवर। केवल आय से पात्रता तय नहीं होती।"},
        "marathi": {"name": "आयुष्मान भारत प्रधानमंत्री जन आरोग्य योजना", "short_name": "आयुष्मान भारत", "benefit": "पात्र कुटुंबांना सूचीबद्ध रुग्णालयांत दरवर्षी प्रति कुटुंब ₹5 लाखांपर्यंत रोखरहित उपचार मिळू शकतात।", "summary": "PM-JAY च्या अधिकृत लाभार्थी यादीतील कुटुंबांसाठी रुग्णालय उपचार कवच. केवळ उत्पन्नावर पात्रता ठरत नाही."},
    },
    {
        "id": "nsp-post-matric",
        "name": "National Scholarship Portal",
        "short_name": "Post-matric scholarships",
        "category": "Education",
        "benefit": "Central and state scholarships may help with eligible students' education costs.",
        "summary": "The National Scholarship Portal lists multiple central and state scholarships. Rules, income limits, and deadlines differ by scheme and state.",
        "state": "Central and state schemes",
        "official_url": "https://scholarships.gov.in/",
        "apply_at": "National Scholarship Portal or your school's / college's scholarship office",
        "documents": ["Aadhaar or accepted identity document", "Previous examination marksheet", "Income certificate (if required)", "Category certificate (if required)", "Bank account details"],
        "steps": ["Use the official portal's scheme finder to select your state, course, and category.", "Read the specific scheme's income limit, deadlines, and document rules.", "Submit the application and provide its ID to your institution for verification."],
        "rules": {"requires_student": True},
        "note": "This is a discovery lead, not confirmation of a particular scholarship. Check scheme-specific rules and deadlines.",
        "hindi": {"name": "राष्ट्रीय छात्रवृत्ति पोर्टल", "short_name": "माध्यमिक-पश्चात छात्रवृत्तियाँ", "benefit": "केंद्र और राज्य की छात्रवृत्तियाँ पात्र विद्यार्थियों की पढ़ाई के खर्च में मदद कर सकती हैं।", "summary": "राष्ट्रीय छात्रवृत्ति पोर्टल पर कई केंद्रीय और राज्य छात्रवृत्तियाँ हैं। नियम, आय सीमा और अंतिम तिथियाँ अलग-अलग हैं।"},
        "marathi": {"name": "राष्ट्रीय शिष्यवृत्ती पोर्टल", "short_name": "शालान्तोत्तर शिष्यवृत्ती", "benefit": "केंद्र आणि राज्य शिष्यवृत्ती पात्र विद्यार्थ्यांच्या शिक्षण खर्चात मदत करू शकतात।", "summary": "राष्ट्रीय शिष्यवृत्ती पोर्टलवर अनेक केंद्र व राज्य शिष्यवृत्ती आहेत. नियम, उत्पन्न मर्यादा आणि मुदती वेगवेगळ्या आहेत."},
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    return connection


@contextmanager
def database_connection():
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def build_static_cache() -> None:
    for route, path in STATIC_FILES.items():
        if not path.is_file():
            continue
        content = path.read_bytes()
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else (
            "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8"
        )
        STATIC_CACHE[route] = (content, content_type)


def get_cached_scheme_catalog() -> list[dict]:
    now = time.monotonic()
    with CATALOG_CACHE_LOCK:
        if now < CATALOG_CACHE["expires_at"]:
            return CATALOG_CACHE["value"]
    with database_connection() as connection:
        schemes = connection.execute(
            """SELECT s.id,s.name,s.short_name,s.category,s.benefit,s.summary,s.state,
                      s.official_url,s.apply_at,s.note,r.min_age,r.max_age,
                      r.required_occupation,r.required_gender,r.requires_land,
                      r.requires_no_lpg,r.requires_bank_account,r.requires_student,
                      r.manual_verification
               FROM schemes s JOIN eligibility_rules r ON r.scheme_id=s.id
               ORDER BY s.category,s.name"""
        ).fetchall()
        catalog = []
        for row in schemes:
            scheme = dict(row)
            scheme["rules"] = {
                key: scheme.pop(key)
                for key in (
                    "min_age", "max_age", "required_occupation", "required_gender",
                    "requires_land", "requires_no_lpg", "requires_bank_account",
                    "requires_student", "manual_verification",
                )
            }
            scheme["documents"] = [
                document["name"]
                for document in connection.execute(
                    """SELECT d.name FROM documents d
                       JOIN scheme_documents sd ON sd.document_id=d.id
                       WHERE sd.scheme_id=? ORDER BY d.id""",
                    (scheme["id"],),
                )
            ]
            scheme["steps"] = [
                step["instruction"]
                for step in connection.execute(
                    """SELECT instruction FROM application_steps
                       WHERE scheme_id=? ORDER BY position""",
                    (scheme["id"],),
                )
            ]
            source = next((item for item in SCHEMES if item["id"] == scheme["id"]), None)
            scheme["translations"] = {
                locale: source[translation]
                for locale, translation in (("hi", "hindi"), ("mr", "marathi"))
                if source and source.get(translation)
            }
            scheme["beneficiary_stat"] = OFFICIAL_BENEFICIARY_STATS.get(scheme["id"])
            catalog.append(scheme)
    with CATALOG_CACHE_LOCK:
        CATALOG_CACHE["value"] = catalog
        CATALOG_CACHE["expires_at"] = now + 30.0
    return catalog


def initialize_database() -> None:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with database_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schemes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                short_name TEXT NOT NULL,
                category TEXT NOT NULL,
                benefit TEXT NOT NULL,
                summary TEXT NOT NULL,
                state TEXT NOT NULL,
                official_url TEXT NOT NULL,
                apply_at TEXT NOT NULL,
                note TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eligibility_rules (
                scheme_id TEXT PRIMARY KEY REFERENCES schemes(id) ON DELETE CASCADE,
                min_age INTEGER,
                max_age INTEGER,
                required_occupation TEXT,
                required_gender TEXT,
                requires_land INTEGER NOT NULL DEFAULT 0,
                requires_no_lpg INTEGER NOT NULL DEFAULT 0,
                requires_bank_account INTEGER NOT NULL DEFAULT 0,
                requires_student INTEGER NOT NULL DEFAULT 0,
                manual_verification INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS scheme_documents (
                scheme_id TEXT NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id),
                PRIMARY KEY (scheme_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS application_steps (
                scheme_id TEXT NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                instruction TEXT NOT NULL,
                PRIMARY KEY (scheme_id, position)
            );
            CREATE TABLE IF NOT EXISTS helper_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                state TEXT NOT NULL,
                age INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                result_count INTEGER NOT NULL,
                owner_email TEXT NOT NULL DEFAULT 'local-demo@example.invalid',
                owner_id TEXT NOT NULL DEFAULT 'demo:local-demo'
            );
            """
        )
        helper_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(helper_checks)")
        }
        if "owner_email" not in helper_columns:
            connection.execute(
                "ALTER TABLE helper_checks ADD COLUMN owner_email TEXT NOT NULL "
                "DEFAULT 'local-demo@example.invalid'"
            )
        if "owner_id" not in helper_columns:
            connection.execute(
                "ALTER TABLE helper_checks ADD COLUMN owner_id TEXT NOT NULL "
                "DEFAULT 'demo:local-demo'"
            )
            connection.execute(
                """UPDATE helper_checks
                   SET owner_id = CASE
                       WHEN lower(trim(owner_email)) = 'local-demo@example.invalid'
                           THEN 'demo:local-demo'
                       ELSE 'legacy-email:' || lower(trim(owner_email))
                   END"""
            )
        for scheme in SCHEMES:
            connection.execute(
                """INSERT OR IGNORE INTO schemes
                (id,name,short_name,category,benefit,summary,state,official_url,apply_at,note)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(scheme[key] for key in (
                    "id", "name", "short_name", "category", "benefit", "summary",
                    "state", "official_url", "apply_at", "note",
                )),
            )
            rules = scheme["rules"]
            connection.execute(
                """INSERT OR IGNORE INTO eligibility_rules
                (scheme_id,min_age,max_age,required_occupation,required_gender,requires_land,
                 requires_no_lpg,requires_bank_account,requires_student,manual_verification)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    scheme["id"], rules.get("min_age"), rules.get("max_age"),
                    rules.get("required_occupation"), rules.get("required_gender"),
                    int(rules.get("requires_land", False)), int(rules.get("requires_no_lpg", False)),
                    int(rules.get("requires_bank_account", False)), int(rules.get("requires_student", False)),
                    int(rules.get("manual_verification", False)),
                ),
            )
            for document in scheme["documents"]:
                connection.execute("INSERT OR IGNORE INTO documents (name) VALUES (?)", (document,))
                document_row = connection.execute(
                    "SELECT id FROM documents WHERE name = ?", (document,)
                ).fetchone()
                connection.execute(
                    "INSERT OR IGNORE INTO scheme_documents (scheme_id,document_id) VALUES (?,?)",
                    (scheme["id"], document_row["id"]),
                )
            for position, instruction in enumerate(scheme["steps"], start=1):
                connection.execute(
                    "INSERT OR IGNORE INTO application_steps (scheme_id,position,instruction) VALUES (?,?,?)",
                    (scheme["id"], position, instruction),
                )


def validate_profile(profile: object) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")
    try:
        age = int(profile.get("age", -1))
        income = int(profile.get("annual_income", -1))
    except (TypeError, ValueError) as error:
        raise ValueError("Age and annual income must be whole numbers.") from error
    if not 0 <= age <= 120:
        raise ValueError("Age must be between 0 and 120.")
    if not 0 <= income <= 100_000_000:
        raise ValueError("Annual household income must be between ₹0 and ₹10 crore.")
    state = profile.get("state")
    if state not in STATES:
        raise ValueError("Select a valid Indian state or union territory.")
    allowed = {
        "gender": {"woman", "man", "other", "prefer_not_to_say"},
        "category": {"general", "sc", "st", "obc", "other", "prefer_not_to_say"},
        "occupation": {"farmer", "student", "worker", "other"},
    }
    validated = {"age": age, "annual_income": income, "state": state}
    for key, options in allowed.items():
        value = profile.get(key)
        if value not in options:
            raise ValueError(f"Select a valid {key.replace('_', ' ')}.")
        validated[key] = value
    for key in ("owns_land", "has_lpg", "has_bank_account", "is_student", "is_taxpayer"):
        value = profile.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} must be yes or no.")
        validated[key] = value
    return validated


def evaluate_profile(profile: dict, connection: sqlite3.Connection) -> list[dict]:
    profile = validate_profile(profile)
    rows = connection.execute(
        """SELECT s.*, r.min_age, r.max_age, r.required_occupation, r.required_gender,
                  r.requires_land, r.requires_no_lpg, r.requires_bank_account,
                  r.requires_student, r.manual_verification
           FROM schemes s JOIN eligibility_rules r ON r.scheme_id = s.id
           ORDER BY s.name"""
    ).fetchall()
    matches = []
    for row in rows:
        rule = dict(row)
        reasons = []
        possible = True
        if rule["manual_verification"]:
            reasons.append("Eligibility is decided from an official beneficiary list; please check it directly.")
        if rule["min_age"] is not None and profile["age"] < rule["min_age"]:
            possible = False
        if rule["max_age"] is not None and profile["age"] > rule["max_age"]:
            possible = False
        if rule["required_gender"] and profile["gender"] != rule["required_gender"]:
            possible = False
        if rule["required_occupation"] and profile["occupation"] != rule["required_occupation"]:
            possible = False
        if rule["requires_land"] and not profile["owns_land"]:
            possible = False
        if rule["requires_no_lpg"] and (profile["has_lpg"] or profile["gender"] != "woman"):
            possible = False
        if rule["requires_bank_account"] and not profile["has_bank_account"]:
            possible = False
        if rule["requires_student"] and not profile["is_student"]:
            possible = False
        if rule["id"] == "atal-pension-yojana" and profile["is_taxpayer"]:
            possible = False
        if not possible:
            continue
        if rule["id"] == "pm-kisan":
            reasons.append("You said you are a farmer and have land. The official land and exclusion checks still apply.")
        elif rule["id"] == "pm-ujjwala":
            reasons.append("Your answers fit the basic adult-woman / no-existing-LPG conditions. Household eligibility must be verified.")
        elif rule["id"] == "atal-pension-yojana":
            reasons.append("Your age and bank-account answers fit the basic joining conditions. Confirm current rules with your bank.")
        elif rule["id"] == "nsp-post-matric":
            reasons.append("You said you are a student. Use the portal to find a scholarship matching your course, state, and category.")
        document_rows = connection.execute(
            """SELECT d.name FROM documents d JOIN scheme_documents sd ON sd.document_id=d.id
               WHERE sd.scheme_id=? ORDER BY d.id""",
            (rule["id"],),
        ).fetchall()
        step_rows = connection.execute(
            "SELECT instruction FROM application_steps WHERE scheme_id=? ORDER BY position",
            (rule["id"],),
        ).fetchall()
        item = {
            key: rule[key] for key in (
                "id", "name", "short_name", "category", "benefit", "summary",
                "state", "official_url", "apply_at", "note",
            )
        }
        scheme_data = next(scheme for scheme in SCHEMES if scheme["id"] == rule["id"])
        item.update({
            "status": "verify" if rule["manual_verification"] else "possible",
            "reasons": reasons,
            "documents": [document["name"] for document in document_rows],
            "steps": [step["instruction"] for step in step_rows],
            "translations": {
                locale: scheme_data[translation]
                for locale, translation in (("hi", "hindi"), ("mr", "marathi"))
                if scheme_data.get(translation)
            },
        })
        matches.append(item)
    return matches


def verify_firebase_id_token(id_token: object) -> dict:
    if not isinstance(id_token, str) or not id_token or len(id_token) > 10_000:
        raise ValueError("Sign-in did not return a valid identity token.")
    if not FIREBASE_AUTH_CONFIGURED:
        raise ValueError("Mobile sign-in is not configured. Follow the Firebase setup instructions.")
    url = (
        "https://identitytoolkit.googleapis.com/v1/accounts:lookup?"
        + urllib.parse.urlencode({"key": FIREBASE_API_KEY})
    )
    body = json.dumps({"idToken": id_token}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            identity = json.loads(response.read(32_768))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Your mobile number could not be verified. Please try again.") from error
    if not isinstance(identity, dict) or not isinstance(identity.get("users"), list):
        raise ValueError("The identity provider returned an invalid verification response.")
    if not identity["users"] or not isinstance(identity["users"][0], dict):
        raise ValueError("This phone account could not be verified.")
    claims = identity["users"][0]
    local_id = claims.get("localId")
    if not isinstance(local_id, str) or not local_id or len(local_id) > 128:
        raise ValueError("The identity provider returned an invalid account.")
    email = claims.get("email", "")
    if not isinstance(email, str) or len(email) > 254:
        email = ""
    email = email.strip().casefold()
    phone = claims.get("phoneNumber", "")
    if (
        not isinstance(phone, str)
        or not 8 <= len(phone) <= 16
        or not phone.startswith("+")
        or not phone[1:].isdigit()
    ):
        phone = ""
    if not phone:
        raise ValueError("Sign in with a verified mobile number to continue.")
    display_name = claims.get("displayName", "")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = email or phone
    return {
        "id": f"firebase:{local_id}",
        "email": email,
        "phone": phone,
        "name": display_name.strip()[:120],
        "demo": False,
    }


def session_cookie_headers(
    token: str, max_age: int = SESSION_TTL_SECONDS, *, secure: bool = SECURE_COOKIES
) -> str:
    secure_attribute = "; Secure" if secure else ""
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax{secure_attribute}; Max-Age={max_age}"


class AssistantServiceError(Exception):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def generate_assistant_reply(messages: list[dict], language: str) -> str:
    if not GEMINI_API_KEY:
        raise AssistantServiceError(
            "The AI helper is not configured yet. Add a Gemini API key in the app's private server settings.",
            503,
        )

    language_name = LANGUAGE_NAMES[language]
    system_instruction = (
        "You are YojanaVaani, a kind and concise assistant helping people understand Indian "
        "government welfare schemes and practical next steps. Respond in "
        f"{language_name}; if the user clearly writes in a different language, follow that language. "
        "Use simple, respectful language and short paragraphs suitable for spoken replies. "
        "Do not claim a person is eligible or that an application is approved. Scheme rules, "
        "benefit totals, documents, and deadlines may change; do not invent current facts, "
        "statistics, contacts, or links. Explain uncertainty and direct users to the official "
        "scheme portal or an authorised centre for confirmation. Applications are free; warn "
        "against agents promising guaranteed approval. Never ask for Aadhaar numbers, OTPs, "
        "PINs, passwords, bank account numbers, or other sensitive identifiers. If the user "
        "shares one, tell them not to share it and do not repeat it. This is general information, "
        "not an official government service. Keep replies under 150 words."
    )
    body = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [
            {
                "role": "model" if message["role"] == "assistant" else "user",
                "parts": [{"text": message["text"]}],
            }
            for message in messages
        ],
        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 512},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(64_000))
    except urllib.error.HTTPError as error:
        error.read(4_096)
        if error.code == 429:
            raise AssistantServiceError(
                "The AI helper is busy or has reached its free-tier limit. Please try again later.",
                503,
            ) from error
        raise AssistantServiceError(
            "The AI helper could not complete that request. Please try again shortly.",
            502,
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise AssistantServiceError(
            "The AI helper could not be reached. Check your connection and try again.",
            502,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AssistantServiceError(
            "The AI helper returned an unreadable response. Please try again.",
            502,
        ) from error

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise AssistantServiceError(
            "The AI helper returned no answer. Please rephrase your question and try again.",
            502,
        ) from error
    if not text:
        raise AssistantServiceError(
            "The AI helper returned no answer. Please rephrase your question and try again.",
            502,
        )
    return text


class YojanaHandler(BaseHTTPRequestHandler):
    server_version = "YojanaVaani/1.0"

    def _send_json(self, data: object, status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json_with_cookie(self, data: object, cookie: str, status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _current_user(self) -> dict | None:
        cookie = self.headers.get("Cookie", "")
        token = ""
        for item in cookie.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == SESSION_COOKIE:
                token = value
                break
        if not token:
            return None
        session_key = hashlib.sha256(token.encode()).hexdigest()
        session = SESSIONS.get(session_key)
        if not session:
            return None
        if session["expires_at"] <= time.time():
            SESSIONS.pop(session_key, None)
            return None
        return session["user"]

    def _require_user(self) -> dict | None:
        user = self._current_user()
        if user is None:
            self._send_json({"error": "Sign in to use YojanaVaani.", "code": "authentication_required"}, 401)
        return user

    def _valid_same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed_origin = urlsplit(origin)
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip()
        return (
            parsed_origin.scheme == forwarded_proto
            and parsed_origin.netloc.casefold() == self.headers.get("Host", "").casefold()
            and forwarded_proto in {"http", "https"}
        )

    def _start_session(self, user: dict) -> None:
        token = secrets.token_urlsafe(32)
        session_key = hashlib.sha256(token.encode()).hexdigest()
        SESSIONS[session_key] = {"user": user, "expires_at": time.time() + SESSION_TTL_SECONDS}
        self._send_json_with_cookie(
            {"authenticated": True, "user": user},
            session_cookie_headers(token),
        )

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request size.") from error
        if length < 1 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is empty or too large.")
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Request body must contain valid JSON.") from error
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _discard_request_body(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            return
        if length:
            self.rfile.read(length)

    def _serve_static(self, path: Path, *, head_only: bool = False) -> None:
        if not path.is_file():
            self._send_json({"error": "Not found."}, 404)
            return
        key = next((route for route, file_path in STATIC_FILES.items() if file_path == path), str(path))
        cached = STATIC_CACHE.get(key)
        if cached is not None:
            content, content_type = cached
        else:
            content = path.read_bytes()
            content_type = "text/html; charset=utf-8" if path.suffix == ".html" else (
                "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8"
            )
            STATIC_CACHE[key] = (content, content_type)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _handle_head_or_get(self, *, head_only: bool) -> None:
        request = urlsplit(self.path)
        if request.path in STATIC_FILES:
            self._serve_static(STATIC_FILES[request.path], head_only=head_only)
        elif request.path == "/api/health":
            self._send_json({"ok": True, "service": "YojanaVaani", "mode": "production"}, 200)
        elif request.path == "/api/auth/config":
            self._send_json({
                "firebase_configured": FIREBASE_AUTH_CONFIGURED,
                "firebase": FIREBASE_CONFIG if FIREBASE_AUTH_CONFIGURED else None,
                "assistant_configured": bool(GEMINI_API_KEY),
            })
        elif request.path == "/api/auth/me":
            user = self._current_user()
            self._send_json({"authenticated": bool(user), "user": user})
        elif request.path == "/api/auth/logout":
            self._send_json({"error": "Use POST to sign out."}, 405)
        elif request.path == "/api/states":
            if not self._require_user():
                return
            self._send_json(STATES)
        elif request.path == "/api/schemes":
            if not self._require_user():
                return
            self._send_json(get_cached_scheme_catalog())
        elif request.path == "/api/helper/records":
            user = self._require_user()
            if not user:
                return
            with database_connection() as connection:
                rows = connection.execute(
                    """SELECT id,label,state,age,created_at,result_count FROM helper_checks
                       WHERE owner_id=? ORDER BY id DESC LIMIT 50""",
                    (user["id"],),
                ).fetchall()
            self._send_json([dict(row) for row in rows])
        else:
            self._send_json({"error": "Not found."}, 404)

    def do_GET(self) -> None:
        self._handle_head_or_get(head_only=False)

    def do_HEAD(self) -> None:
        self._handle_head_or_get(head_only=True)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Cookie")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if route not in {
            "/api/auth/firebase", "/api/auth/logout",
            "/api/eligibility/check", "/api/helper/batch", "/api/assistant/chat",
        }:
            self._discard_request_body()
            self._send_json({"error": "Not found."}, 404)
            return
        if not self._valid_same_origin():
            self._discard_request_body()
            self._send_json({"error": "Request origin could not be verified."}, 403)
            return
        try:
            payload = self._read_json()
            if route == "/api/auth/firebase":
                self._start_session(verify_firebase_id_token(payload.get("id_token")))
                return
            if route == "/api/auth/logout":
                user = self._current_user()
                if user:
                    cookie_header = self.headers.get("Cookie", "")
                    for item in cookie_header.split(";"):
                        name, separator, token = item.strip().partition("=")
                        if separator and name == SESSION_COOKIE:
                            key = hashlib.sha256(token.encode()).hexdigest()
                            SESSIONS.pop(key, None)
                            break
                self._send_json_with_cookie(
                    {"authenticated": False},
                    session_cookie_headers("", 0),
                )
                return
            user = self._require_user()
            if not user:
                return
            if route == "/api/assistant/chat":
                language = payload.get("language")
                messages = payload.get("messages")
                if not isinstance(language, str) or language not in LANGUAGE_NAMES:
                    raise ValueError("Choose a supported assistant language.")
                if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
                    raise ValueError("Send between 1 and 8 recent chat messages.")
                clean_messages = []
                total_characters = 0
                for index, message in enumerate(messages, start=1):
                    if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in {"user", "assistant"}:
                        raise ValueError(f"Message {index} is invalid.")
                    text = message.get("text")
                    if not isinstance(text, str) or not text.strip() or len(text) > 1_500:
                        raise ValueError(f"Message {index} must contain 1 to 1,500 characters.")
                    total_characters += len(text)
                    clean_messages.append({"role": message["role"], "text": text.strip()})
                if total_characters > 6_000:
                    raise ValueError("The recent conversation is too long. Start a new chat.")
                if clean_messages[-1]["role"] != "user":
                    raise ValueError("The latest chat message must be your question.")
                now = time.monotonic()
                with ASSISTANT_REQUESTS_LOCK:
                    recent_requests = [
                        timestamp
                        for timestamp in ASSISTANT_REQUESTS.get(user["id"], [])
                        if now - timestamp < ASSISTANT_REQUEST_WINDOW
                    ]
                    is_limited = len(recent_requests) >= ASSISTANT_REQUEST_LIMIT
                    if not is_limited:
                        recent_requests.append(now)
                        ASSISTANT_REQUESTS[user["id"]] = recent_requests
                    if len(ASSISTANT_REQUESTS) > 5_000:
                        for session_id, timestamps in list(ASSISTANT_REQUESTS.items()):
                            fresh = [
                                timestamp
                                for timestamp in timestamps
                                if now - timestamp < ASSISTANT_REQUEST_WINDOW
                            ]
                            if fresh:
                                ASSISTANT_REQUESTS[session_id] = fresh
                            else:
                                ASSISTANT_REQUESTS.pop(session_id, None)
                if is_limited:
                    self._send_json(
                        {"error": "The AI helper is being used often. Please wait a minute before asking again."},
                        429,
                    )
                    return
                reply = generate_assistant_reply(clean_messages, language)
                self._send_json({"reply": reply, "language": language})
                return
            if route == "/api/eligibility/check":
                profile = validate_profile(payload)
                with database_connection() as connection:
                    matches = evaluate_profile(profile, connection)
                self._send_json({"matches": matches, "checked_at": now_iso(), "saved": False})
                return
            families = payload.get("families")
            if not isinstance(families, list) or not 1 <= len(families) <= 50:
                raise ValueError("Submit between 1 and 50 family profiles at a time.")
            saved = []
            with database_connection() as connection:
                connection.execute("BEGIN")
                for index, family in enumerate(families, start=1):
                    if not isinstance(family, dict):
                        raise ValueError(f"Family {index} must be a profile object.")
                    label = family.get("label", f"Family {index}")
                    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                        raise ValueError(f"Family {index} needs a label of 1 to 80 characters.")
                    profile = validate_profile(family)
                    matches = evaluate_profile(profile, connection)
                    cursor = connection.execute(
                        """INSERT INTO helper_checks
                           (label,state,age,created_at,result_count,owner_id) VALUES (?,?,?,?,?,?)""",
                        (label.strip(), profile["state"], profile["age"], now_iso(), len(matches), user["id"]),
                    )
                    saved.append({"id": cursor.lastrowid, "label": label.strip(), "matches": matches})
            self._send_json({"saved": saved, "count": len(saved)}, 201)
        except ValueError as error:
            self._send_json({"error": str(error)}, 400)
        except AssistantServiceError as error:
            self._send_json({"error": str(error)}, error.status)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} - {format % args}")


def main() -> None:
    initialize_database()
    build_static_cache()
    server = ThreadingHTTPServer((HOST, PORT), YojanaHandler)
    print(f"YojanaVaani is listening on {HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping YojanaVaani.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
