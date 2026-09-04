import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.dirname(__file__), 'bis_compliance.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema_compatibility():
    conn = get_db_connection()
    cursor = conn.cursor()

    user_cols = {row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()}
    for column_name, column_sql in {
        'phone': 'TEXT',
        'city': 'TEXT',
        'state': 'TEXT',
        'country': 'TEXT DEFAULT "India"',
        'user_type': 'TEXT',
        'business_stage': 'TEXT',
        'product_category': 'TEXT',
        'product_name': 'TEXT',
        'product_description': 'TEXT',
        'monthly_quantity': 'TEXT',
        'profile_completed': 'INTEGER DEFAULT 0'
    }.items():
        if column_name not in user_cols:
            cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_sql}')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        user_type TEXT,
        business_stage TEXT,
        company_name TEXT,
        product_category TEXT,
        product_name TEXT,
        product_description TEXT,
        monthly_quantity TEXT,
        city TEXT,
        state TEXT,
        country TEXT DEFAULT 'India',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')

    # compliance_cases gains the "active product workspace" columns
    case_cols = {row[1] for row in cursor.execute("PRAGMA table_info(compliance_cases)").fetchall()}
    for col, ddl in {
        'product_slug': 'TEXT',
        'user_type': 'TEXT',
        'city': 'TEXT',
        'state': 'TEXT',
        'saved_areas_json': 'TEXT',
    }.items():
        if col not in case_cols:
            cursor.execute(f'ALTER TABLE compliance_cases ADD COLUMN {col} {ddl}')

    # laboratories gains a real state column (code already reads lab.get('state'))
    lab_cols = {row[1] for row in cursor.execute("PRAGMA table_info(laboratories)").fetchall()}
    if 'state' not in lab_cols:
        cursor.execute('ALTER TABLE laboratories ADD COLUMN state TEXT')

    # documents / document_chunks get product scoping for verbatim-clause retrieval
    doc_cols = {row[1] for row in cursor.execute("PRAGMA table_info(documents)").fetchall()}
    if 'product_slug' not in doc_cols:
        cursor.execute('ALTER TABLE documents ADD COLUMN product_slug TEXT')
    if 'source_url' not in doc_cols:
        cursor.execute('ALTER TABLE documents ADD COLUMN source_url TEXT')
    ch_cols = {row[1] for row in cursor.execute("PRAGMA table_info(document_chunks)").fetchall()}
    if 'product_slug' not in ch_cols:
        cursor.execute('ALTER TABLE document_chunks ADD COLUMN product_slug TEXT')
    if 'source_url' not in ch_cols:
        cursor.execute('ALTER TABLE document_chunks ADD COLUMN source_url TEXT')

    # search_history (also created in init_db; here for already-migrated DBs)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        case_id INTEGER,
        product_slug TEXT,
        query TEXT NOT NULL,
        mode TEXT,
        answer_md TEXT,
        sources_json TEXT,
        area TEXT,
        language TEXT DEFAULT 'en',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    conn.commit()
    conn.close()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        company_name TEXT,
        role TEXT DEFAULT 'manufacturer',
        auth_provider TEXT DEFAULT 'email',
        phone TEXT,
        city TEXT,
        state TEXT,
        country TEXT DEFAULT 'India',
        user_type TEXT,
        business_stage TEXT,
        product_category TEXT,
        product_name TEXT,
        product_description TEXT,
        monthly_quantity TEXT,
        profile_completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        user_type TEXT,
        business_stage TEXT,
        company_name TEXT,
        product_category TEXT,
        product_name TEXT,
        product_description TEXT,
        monthly_quantity TEXT,
        city TEXT,
        state TEXT,
        country TEXT DEFAULT 'India',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')

    # 2. Enterprise Onboarding Profiles (4-Step Questionnaire Table)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_onboarding_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        persona_role TEXT NOT NULL,
        industry_sector TEXT NOT NULL,
        compliance_stage TEXT NOT NULL,
        product_name TEXT NOT NULL,
        product_description TEXT,
        monthly_production_quantity TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')

    # 3. Documents table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_code TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        url TEXT,
        publication_date TEXT,
        effective_date TEXT,
        version TEXT,
        is_active INTEGER DEFAULT 1
    );
    ''')

    # 4. Document Chunks table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS document_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER,
        doc_code TEXT,
        doc_title TEXT,
        page_number INTEGER,
        section_heading TEXT,
        content TEXT NOT NULL,
        embedding_json TEXT,
        FOREIGN KEY(document_id) REFERENCES documents(id)
    );
    ''')

    # 5. Standards Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS standards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        is_number TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        scope_summary TEXT,
        is_mandatory INTEGER DEFAULT 0,
        applicable_scheme TEXT,
        testing_requirements_json TEXT
    );
    ''')

    # 6. QCOs Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS qcos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_title TEXT NOT NULL,
        standard_is_number TEXT NOT NULL,
        ministry TEXT,
        notification_date TEXT,
        enforcement_date TEXT,
        status TEXT DEFAULT 'Active',
        scheme TEXT DEFAULT 'Scheme I (ISI Mark)',
        summary TEXT
    );
    ''')

    # 7. Laboratories Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS laboratories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lab_name TEXT NOT NULL,
        location TEXT NOT NULL,
        city TEXT,
        contact_email TEXT,
        contact_phone TEXT,
        accreditation TEXT,
        supported_standards_json TEXT
    );
    ''')

    # 8. Chat Sessions & Messages Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        session_title TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        sender TEXT NOT NULL,
        content TEXT NOT NULL,
        citations_json TEXT,
        decision_tree_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
    );
    ''')

    # 9. Compliance Cases Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS compliance_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_name TEXT NOT NULL,
        category TEXT,
        is_number TEXT,
        qco_status TEXT,
        scheme TEXT,
        current_step TEXT DEFAULT 'Standard Identified',
        checklist_json TEXT,
        lab_enquiry_status TEXT DEFAULT 'Not Started',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    ''')

    # 10. Audit Logs Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action_type TEXT NOT NULL,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')

    # 11. Search / AI-assistant history (shown on the left of the personalised Home)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        case_id INTEGER,
        product_slug TEXT,
        query TEXT NOT NULL,
        mode TEXT,
        answer_md TEXT,
        sources_json TEXT,
        area TEXT,
        language TEXT DEFAULT 'en',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(case_id) REFERENCES compliance_cases(id)
    );
    ''')

    conn.commit()
    conn.close()

    # Bring pre-existing databases up to the current column set (city, state,
    # user_type, product_*, profile_completed, ...). Safe no-op when already current.
    ensure_schema_compatibility()

if __name__ == '__main__':
    init_db()
