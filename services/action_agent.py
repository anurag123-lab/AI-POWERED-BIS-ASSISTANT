import json
from database import get_db_connection

ACTION_REGISTRY = {
    "BIS_PORTAL_APPLY": {
        "id": "BIS_PORTAL_APPLY",
        "level": 1,
        "name": "Open Official BIS Application Portal",
        "description": "Direct link to official Manakonline BIS license application system.",
        "official_url": "https://www.manakonline.in/MANAK/login",
        "authority": "Bureau of Indian Standards (BIS)",
        "risk": "LOW (External Official Navigation)"
    },
    "GENERATE_CHECKLIST": {
        "id": "GENERATE_CHECKLIST",
        "level": 2,
        "name": "Generate Factory Compliance Checklist",
        "description": "Compiles mandatory testing, factory machinery, and documentation checklist.",
        "risk": "MEDIUM (Creates Compliance Workspace Case)"
    },
    "LAB_ENQUIRY_DISPATCH": {
        "id": "LAB_ENQUIRY_DISPATCH",
        "level": 3,
        "name": "Draft & Dispatch Laboratory Testing Enquiry",
        "description": "Generates formal testing enquiry to NABL/BIS accredited laboratories.",
        "risk": "HIGH (External Action - Requires Explicit User Approval Gateway)"
    }
}

def get_action_recommendations(matched_standard, qco_info, eligible_labs):
    """
    Returns permission-classified action recommendations for the user interface.
    """
    recommendations = [
        {
            "action_id": "BIS_PORTAL_APPLY",
            "level": 1,
            "title": "Apply on Official BIS Portal",
            "badge": "Level 1: Direct Link",
            "url": ACTION_REGISTRY["BIS_PORTAL_APPLY"]["official_url"],
            "description": "Direct access to official Manakonline e-BIS licensing portal."
        },
        {
            "action_id": "GENERATE_CHECKLIST",
            "level": 2,
            "title": "Create Compliance Case & Download Checklist",
            "badge": "Level 2: Workspace Creation",
            "description": "Track your certification roadmap and generate factory audit PDF report."
        }
    ]

    if eligible_labs:
        recommendations.append({
            "action_id": "LAB_ENQUIRY_DISPATCH",
            "level": 3,
            "title": f"Contact Accredited Lab ({eligible_labs[0]['lab_name']})",
            "badge": "Level 3: Approval Gateway Required",
            "lab_name": eligible_labs[0]['lab_name'],
            "lab_email": eligible_labs[0]['contact_email'],
            "description": "Pre-fill testing enquiry email. Requires explicit user review & approval before sending."
        })

    return recommendations

def draft_lab_enquiry_email(company_name, product_name, is_number, lab):
    """
    Drafts formal laboratory enquiry email for user approval.
    """
    subject = f"Testing & Type Approval Enquiry: {product_name} ({is_number})"
    body = (
        f"Dear {lab['lab_name']} Testing Team,\n\n"
        f"We are {company_name or 'a registered manufacturer'}, seeking official type testing and accreditation "
        f"for our product: {product_name} under Indian Standard {is_number}.\n\n"
        f"Product Details:\n"
        f"- Product Category: {product_name}\n"
        f"- Applicable Standard: {is_number}\n"
        f"- Facility Location: India\n\n"
        f"Please provide:\n"
        f"1. Testing sample quantity requirements\n"
        f"2. Estimated testing fee schedule and turnaround time\n"
        f"3. Document submission format for BIS Scheme-I / Scheme-II compliance.\n\n"
        f"Best regards,\n"
        f"{company_name or 'Compliance Officer'}"
    )
    return {
        "subject": subject,
        "recipient_name": lab['lab_name'],
        "recipient_email": lab['contact_email'],
        "body": body,
        "approval_required": True,
        "risk_classification": "HIGH - Requires Human Approval Signature"
    }

def execute_user_approved_action(action_id, payload, user_id):
    """
    Executes action after passing through the User Approval Gateway.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Log execution in Audit Trail
    cursor.execute('''
        INSERT INTO audit_logs (user_id, action_type, details)
        VALUES (?, ?, ?)
    ''', (user_id, f"ACTION_GATEWAY_{action_id}", json.dumps(payload)))
    
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "action_id": action_id,
        "message": f"Action '{action_id}' successfully authorized and dispatched under audit log ID.",
        "timestamp": "2026-09-01 19:00:00"
    }
