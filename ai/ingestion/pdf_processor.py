import re
import os
import json
import pymupdf
from groq import Groq
from core.models import StructuredRow
from dotenv import load_dotenv
from ingestion.pii_masker import mask_text, unmask_json

def extract_text_from_file(filepath: str) -> str:
    """Extracts text from PDF. If it's a txt file (for testing), just reads it."""
    if filepath.endswith('.txt'):
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
            
    try:
        doc = pymupdf.open(filepath)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return text
    except Exception as e:
        raise ValueError(f"Failed to extract text from PDF: {str(e)}")


def _extract_via_regex(raw_text: str) -> dict:
    """
    Deterministic rule-based extraction fallback.
    Extracts all standard credit and applicant fields from formatted documents.
    """
    res = {}
    
    # Applicant Name / ID
    name_m = re.search(r'(?:Applicant\s*Name|Employee\s*Name|Customer\s*Name|Name)[:\s\n]+([A-Za-z\s\.\-]+)', raw_text, re.IGNORECASE)
    if name_m:
        cand = name_m.group(1).split('\n')[0].strip()
        if cand and len(cand) > 2 and cand.lower() not in ('none', 'salaried', 'loan', 'profile', 'applicant'):
            res['applicantId'] = cand
            res['applicantName'] = cand
            
    # PAN
    pan_m = re.search(r'\b([A-Z]{5}[0-9]{4}[A-Z])\b', raw_text)
    if pan_m:
        res['pan'] = pan_m.group(1)
        if 'applicantId' not in res:
            res['applicantId'] = pan_m.group(1)

    # Age
    age_m = re.search(r'(?:Age|Applicant\s*Age)[:\s\n]+(\d{1,2})\b', raw_text, re.IGNORECASE)
    if age_m:
        res['age'] = int(age_m.group(1))

    # Employment Type
    emp_m = re.search(r'(?:Employment\s*Type|Occupation)[:\s\n]+(Salaried|Self[\s\-]*Employed|Business|Proprietor)', raw_text, re.IGNORECASE)
    if emp_m:
        cand_emp = emp_m.group(1).strip()
        res['employmentType'] = "Self-Employed" if "self" in cand_emp.lower() else "Salaried"
    elif "salary slip" in raw_text.lower() or "payslip" in raw_text.lower():
        res['employmentType'] = "Salaried"

    # Requested Loan Amount
    loan_m = re.search(r'(?:Requested\s*Loan\s*Amount|Loan\s*Amount|Requested\s*Amount)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if loan_m:
        res['requestedLoanAmount'] = float(loan_m.group(1).replace(',', ''))

    # Requested Tenure
    tenure_m = re.search(r'(?:Requested\s*Tenure|Loan\s*Tenure|Tenure\s*\(Months\)|Tenure)[:\s\n]+(\d+)', raw_text, re.IGNORECASE)
    if tenure_m:
        res['requestedTenure'] = int(tenure_m.group(1))

    # Declared Annual Income
    ann_income_m = re.search(r'(?:Declared\s*Annual\s*Income|Annual\s*Income|Gross\s*Total\s*Income|Total\s*Income|Gross\s*Earnings)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if ann_income_m:
        res['declaredIncome'] = float(ann_income_m.group(1).replace(',', ''))

    # Monthly Salary / Net Pay / Monthly Income
    m_income_m = re.search(r'(?:Net\s*Payable\s*Salary|Net\s*Pay|Monthly\s*Salary|Monthly\s*Income)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if m_income_m:
        res['monthlyIncome'] = float(m_income_m.group(1).replace(',', ''))
        if 'declaredIncome' not in res:
            res['declaredIncome'] = res['monthlyIncome'] * 12

    # Existing Monthly EMI
    emi_m = re.search(r'(?:Existing\s*Monthly\s*EMI|Monthly\s*EMI|Existing\s*EMI|EMI)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if emi_m:
        res['emiDebits'] = float(emi_m.group(1).replace(',', ''))

    # CIBIL Score
    cibil_m = re.search(r'(?:CIBIL\s*Score|Credit\s*Score|CIBIL)[:\s\n]+(\d{3})\b', raw_text, re.IGNORECASE)
    if cibil_m:
        c_val = int(cibil_m.group(1))
        if 300 <= c_val <= 900:
            res['bureauScore'] = c_val

    # Average Bank Balance
    bal_m = re.search(r'(?:Average\s*Bank\s*Balance|Bank\s*Avg\s*Balance|Average\s*Balance|Closing\s*Balance)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if bal_m:
        res['bankAvgBalance'] = float(bal_m.group(1).replace(',', ''))

    # Bounce Count
    bounce_m = re.search(r'(?:Bounce\s*Count|Cheque\s*Bounces|Bounces)[:\s\n]+(\d+)\b', raw_text, re.IGNORECASE)
    if bounce_m:
        res['bounceCount'] = int(bounce_m.group(1))

    # Declared Assets
    asset_m = re.search(r'(?:Declared\s*Assets|Total\s*Assets|Assets)[:\s\n]+[^\d\n]*([\d,]+(?:\.\d+)?)', raw_text, re.IGNORECASE)
    if asset_m:
        res['declaredAssets'] = float(asset_m.group(1).replace(',', ''))

    # Defaults / Write-offs
    default_m = re.search(r'(?:write[\s\-]*off|default|settlement)', raw_text, re.IGNORECASE)
    if default_m:
        line = default_m.group(0).lower()
        surrounding = raw_text[max(0, default_m.start() - 20): min(len(raw_text), default_m.end() + 20)].lower()
        if "none" in surrounding or "no" in surrounding or "false" in surrounding:
            res['writeOffFlag'] = False
        else:
            res['writeOffFlag'] = True

    return res


def parse_document(filepath: str, groq_api_key: str = None) -> StructuredRow:
    """
    Robust Hybrid Document Extraction:
    1. Extract text locally via PyMuPDF.
    2. Deterministic regex extraction pass to guarantee essential numbers.
    3. PII-masked LLM structural extraction pass via Groq.
    4. Merge findings: LLM contextual extraction enriched by regex accuracy.
    """
    if not groq_api_key:
        load_dotenv()
        ai_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        web_env = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web", ".env.local")
        load_dotenv(dotenv_path=web_env)
        load_dotenv(dotenv_path=ai_env, override=True)
        groq_api_key = os.environ.get("GROQ_API_KEY")

    # Step 1: Extract text locally
    raw_text = extract_text_from_file(filepath)
    print(f"  [PDF] Extracted {len(raw_text)} characters of text from {os.path.basename(filepath)}.")
    
    # Step 2: Deterministic Baseline Extraction
    regex_data = _extract_via_regex(raw_text)
    
    extracted = dict(regex_data)
    
    # Step 3: LLM Extraction (if Groq key is configured)
    if groq_api_key:
        try:
            # Mask PII locally (Presidio)
            masked_text, pii_mapping = mask_text(raw_text)
            pii_count = len(pii_mapping)
            print(f"  [PII] Masked {pii_count} PII entities. LLM will NOT see real identity data.")
            
            # Send up to 35,000 characters for deep context
            trimmed_text = masked_text[:35000]

            client = Groq(api_key=groq_api_key)

            system_prompt = """You are an expert financial document extraction AI for a credit underwriting system.
The text provided is from a financial document (ITR, bank statement, salary slip, Demat, loan application form, etc.).
The text has been PII-masked — tokens like <<PERSON_abc123>> or <<IN_PAN_def456>> represent real identity data. Keep them as-is.

Extract ALL of the following fields that are present or directly inferable. Return ONLY a valid JSON object.
Use null for any field not present in the document.

JSON Schema:
{
  "applicantId": "string (Application number, Reference number, or Applicant Name / Token)",
  "applicantName": "string or PII token",
  "pan": "string or PII token",
  "age": number,
  "employmentType": "Salaried" or "Self-Employed" or null,
  "requestedLoanAmount": number,
  "requestedTenure": number,
  "declaredIncome": number (annual gross or declared income in rupees),
  "monthlyIncome": number (monthly take-home or salary in rupees),
  "emiDebits": number (existing monthly EMI in rupees),
  "bureauScore": number (CIBIL score between 300 and 900),
  "bankAvgBalance": number (average daily balance in rupees),
  "bounceCount": number (cheque / ECS return count),
  "declaredAssets": number (total assets, investments, mutual funds, demat in rupees),
  "writeOffFlag": boolean (true only if prior writeoff/default is mentioned),
  "incomeTrend": "UP" or "DOWN" or "FLAT",
  "itrIncomeLastTwoYears": [number, number] or null
}"""

            user_prompt = f"Extract all financial and applicant fields from this document:\n\n{trimmed_text}"

            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model="openai/gpt-oss-20b",
                temperature=0.0,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            llm_extracted = json.loads(content)
            
            # Rehydrate PII locally
            llm_extracted = unmask_json(llm_extracted, pii_mapping)
            print(f"  [PII] Rehydrated {pii_count} PII entities back to real values locally.")
            del pii_mapping

            # Merge LLM results with regex baseline (LLM fills in, regex backs up)
            for k, v in llm_extracted.items():
                if v is not None and str(v).strip() != "":
                    extracted[k] = v

        except Exception as e:
            print(f"  [PDF Warning] Groq LLM extraction failed ({e}). Relying on deterministic regex extractor.")
    else:
        print("  [PDF Notice] GROQ_API_KEY not set. Using deterministic regex extractor.")

    # Determine applicantId / Reference
    app_id = (
        extracted.get("applicantId") or 
        extracted.get("applicantName") or 
        extracted.get("pan") or 
        f"PDF-{os.path.splitext(os.path.basename(filepath))[0]}"
    )
    if app_id:
        # Sanitize newlines or extraneous symbols
        app_id = re.sub(r'[\r\n\t]+', ' ', str(app_id)).strip()
        # Clean multi-space
        app_id = re.sub(r'\s+', ' ', app_id)

    # Monthly vs Annual Income reconciliation
    income = extracted.get("declaredIncome")
    monthly = extracted.get("monthlyIncome")
    if income and not monthly:
        monthly = round(income / 12, 2)
    elif monthly and not income:
        income = round(monthly * 12, 2)

    # Build StructuredRow
    return StructuredRow(
        applicantId=app_id,
        age=extracted.get("age"),
        employmentType=extracted.get("employmentType"),
        requestedLoanAmount=extracted.get("requestedLoanAmount"),
        requestedTenure=extracted.get("requestedTenure"),
        declaredIncome=income,
        monthlyCredits=monthly,
        emiDebits=extracted.get("emiDebits"),
        existingObligations=extracted.get("emiDebits"),
        bureauScore=extracted.get("bureauScore"),
        bankAvgBalance=extracted.get("bankAvgBalance"),
        bankAvgCredits=extracted.get("bankAvgBalance"),
        bounceCount=extracted.get("bounceCount", 0) or 0,
        declaredAssets=extracted.get("declaredAssets"),
        writeOffFlag=bool(extracted.get("writeOffFlag", False)),
        incomeTrend=extracted.get("incomeTrend") or "FLAT",
        itrIncomeLastTwoYears=extracted.get("itrIncomeLastTwoYears") or [],
    )

