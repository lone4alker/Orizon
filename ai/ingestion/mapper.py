import re
import uuid
from typing import List, Dict, Any, Tuple, Optional
from core.models import StructuredRow, NormalizedApplicantProfile

CRITICAL_FIELDS = ["applicantId", "requestedLoanAmount", "declaredIncome"]

FIELD_ALIASES = {
  "applicantId": [
    "applicant_id", "applicantid", "customer_id", "customer_no", "customer_number", "id",
    "applicant_ref", "applicantref", "prospectid", "prospect_id", "application_id", "applicationid",
    "app_id", "appid", "ref_no", "reference_id", "ref_id", "lead_id", "loan_id"
  ],
  "declaredIncome": [
    "monthly_income", "monthly_salary", "salary_month", "income", "salary", "declared_income",
    "annual_income", "gross_income", "net_income", "netmonthlyincome", "net_monthly_income", "annualincome"
  ],
  "existingObligations": [
    "monthly_debt", "existing_emi", "monthly_emi", "debt", "obligations", "existing_obligations",
    "current_emi", "total_emi"
  ],
  "bureauScore": [
    "credit_score", "credit_rating", "cibil", "cibil_score", "bureau_score", "creditscore"
  ],
  "businessVintage": [
    "employment_years", "years_employed", "job_tenure", "employment_tenure", "business_vintage",
    "vintage", "time_with_curr_empr", "work_experience"
  ],
  "requestedLoanAmount": [
    "loan_amount", "requested_amount", "amount_requested", "requested_loan_amount", "loan_amt",
    "amount", "applied_amount"
  ],
  "requestedTenure": [
    "tenure", "requested_tenure", "loan_tenure", "months", "tenure_months", "tenuremonths"
  ],
  "age": [
    "age", "applicant_age"
  ],
  "employmentType": [
    "employment_type", "job_type", "employmenttype", "occupation"
  ],
  "writeOffFlag": [
    "write_off_flag", "write_off", "is_written_off", "has_write_off", "haswriteoff", "last_default",
    "lastdefault", "default_flag", "defaultflag"
  ],
  "settlementFlag": [
    "settlement_flag", "is_settled"
  ],
  "defaultFlag": [
    "default_flag", "is_defaulted", "default_indicator"
  ],
  "bankAvgBalance": [
    "avg_bank_balance", "bank_avg_balance", "average_bank_balance", "avgbalance"
  ],
  "bounceCount": [
    "bounce_count", "bounces", "cheque_bounces", "bouncecount", "tot_missed_pmnt"
  ],
  "monthlyCredits": [
    "monthly_credits", "total_credits"
  ],
  "emiDebits": [
    "emi_debits", "monthly_emis", "bank_emi_debits"
  ],
  "largeObligationsCount": [
    "large_obligations", "large_obligations_count", "high_value_debits"
  ],
  "incomeTrend": [
    "income_trend", "salary_trend", "incometrend"
  ],
  "employmentStability": [
    "employment_stability", "job_stability"
  ],
  "utilityPaymentBehaviour": [
    "utility_payment_behaviour", "utility_behaviour", "bill_payments"
  ],
  "declaredAssets": [
    "declared_assets", "mutual_funds", "equities", "total_assets", "assets_value", "asset_value", "assetsvalue"
  ]
}

def normalize_column_name(name: str) -> str:
  # Lowercase, trim, and replace spaces/dashes with underscores
  name = name.strip().lower()
  name = re.sub(r'[\s\-]+', '_', name)
  return name

def _clean_numeric_str(val: Any) -> Any:
  """Clean currency symbols, commas, and formatting from numeric values."""
  if val is None:
    return None
  if isinstance(val, (int, float)):
    return val
  s = str(val).strip()
  if not s or s.lower() in ('none', 'null', 'nan'):
    return None
  cleaned = re.sub(r'[^\d.-]', '', s)
  try:
    if '.' in cleaned:
      return float(cleaned)
    return int(cleaned)
  except ValueError:
    return val

def schema_mapping(raw_dict: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
  mapped_data = {}
  unmapped = []
  
  # Flatten alias map for quick O(1) lookup: normalized_alias -> canonical_field
  alias_to_canonical = {}
  for canonical, aliases in FIELD_ALIASES.items():
    alias_to_canonical[canonical.lower()] = canonical
    for alias in aliases:
      alias_to_canonical[alias] = canonical
      
  for raw_key, value in raw_dict.items():
    if not raw_key: 
      continue
    
    norm_key = normalize_column_name(raw_key)
    if norm_key in alias_to_canonical:
      canonical_key = alias_to_canonical[norm_key]
      mapped_data[canonical_key] = value
    else:
      # If we don't recognize it, it goes to unmapped and optionally carries over
      unmapped.append(raw_key)
      # We still keep it around for `extra='allow'` just in case, but flag it
      mapped_data[raw_key] = value

  return mapped_data, unmapped

def find_missing_critical_fields(row: Dict[str, Any]) -> List[str]:
  missing = []
  for field in CRITICAL_FIELDS:
    val = row.get(field)
    if val is None or str(val).strip() == "" or str(val).strip().lower() in ('none', 'null', 'nan'):
      missing.append(field)
  return missing

def semantic_validation(profile_dict: Dict[str, Any]) -> List[str]:
  errors = []
  
  # 1. Type validation and range bounds
  score = profile_dict.get('bureauScore')
  if score is not None and str(score).strip() != "":
    try:
      score_val = int(float(_clean_numeric_str(score)))
      if score_val < 300 or score_val > 900:
        errors.append(f"bureauScore {score_val} is out of bounds (300-900)")
      profile_dict['bureauScore'] = score_val
    except (ValueError, TypeError):
      errors.append(f"bureauScore must be an integer, got {score}")

  income = profile_dict.get('declaredIncome')
  if income is not None and str(income).strip() != "":
    try:
      income_val = float(_clean_numeric_str(income))
      if income_val < 0:
        errors.append("declaredIncome cannot be negative")
      profile_dict['declaredIncome'] = income_val
    except (ValueError, TypeError):
      errors.append(f"declaredIncome must be numeric, got {income}")

  debt = profile_dict.get('existingObligations')
  if debt is not None and str(debt).strip() != "":
    try:
      debt_val = float(_clean_numeric_str(debt))
      if debt_val < 0:
        errors.append("existingObligations cannot be negative")
      profile_dict['existingObligations'] = debt_val
    except (ValueError, TypeError):
      errors.append(f"existingObligations must be numeric, got {debt}")

  loan_amt = profile_dict.get('requestedLoanAmount')
  if loan_amt is not None and str(loan_amt).strip() != "":
    try:
      profile_dict['requestedLoanAmount'] = float(_clean_numeric_str(loan_amt))
    except (ValueError, TypeError):
      errors.append(f"requestedLoanAmount must be numeric, got {loan_amt}")

  # 2. Cross-field validation (e.g. DTI)
  if 'declaredIncome' in profile_dict and 'existingObligations' in profile_dict:
    inc = profile_dict.get('declaredIncome')
    dbt = profile_dict.get('existingObligations')
    if isinstance(inc, (int, float)) and isinstance(dbt, (int, float)):
      if inc > 0:
        dti = dbt / inc
        if dti > 1.0:
          errors.append(f"DTI is too high (>100%): {dti*100:.1f}%")
      elif dbt > 0:
        errors.append("Existing obligations > 0 but income is 0")

  return errors

def map_structured_input(raw_dict: Dict[str, Any]) -> NormalizedApplicantProfile:
  # 1. Normalize and map schema (fast path — alias dictionary)
  mapped_dict, unmapped_fields = schema_mapping(raw_dict)
  
  # Auto-resolve applicantId if not directly mapped
  if not mapped_dict.get('applicantId'):
    # Check if raw_dict has any key resembling ref, applicant, prospect, or id
    found_id = None
    for k, v in raw_dict.items():
      norm_k = normalize_column_name(k)
      if any(token in norm_k for token in ('applicant', 'prospect', 'ref', 'customer', 'lead', 'client')) and v:
        found_id = str(v).strip()
        break
    mapped_dict['applicantId'] = found_id if found_id else f"APP-{uuid.uuid4().hex[:8]}"

  # 1b. AI Fallback — if there are unmapped fields, try the smart mapper
  if unmapped_fields:
    try:
      from ingestion.smart_mapper import generate_column_mapping, CANONICAL_FIELDS
      print(f"  [Mapper] {len(unmapped_fields)} unmapped fields. Calling AI fallback...")
      ai_mapping = generate_column_mapping(unmapped_fields, target_fields=CANONICAL_FIELDS)
      
      for raw_key, canonical_key in ai_mapping.items():
        if canonical_key is not None and canonical_key in [f for f in CANONICAL_FIELDS]:
          # Move the value from the raw key to the canonical key
          if raw_key in mapped_dict:
            mapped_dict[canonical_key] = mapped_dict.pop(raw_key)
          elif raw_key in raw_dict:
            mapped_dict[canonical_key] = raw_dict[raw_key]
          if raw_key in unmapped_fields:
            unmapped_fields.remove(raw_key)
      print(f"  [Mapper] AI resolved. Remaining unmapped: {unmapped_fields}")
    except Exception as e:
      print(f"  [Mapper] AI fallback failed: {str(e)}. Continuing with partial mapping.")
  
  # Ensure dpdHistory and itrIncomeLastTwoYears are lists if provided as strings in CSV
  if isinstance(mapped_dict.get('dpdHistory'), str):
    mapped_dict['dpdHistory'] = []
  if isinstance(mapped_dict.get('itrIncomeLastTwoYears'), str):
    mapped_dict['itrIncomeLastTwoYears'] = []
  
  # Clean numeric critical fields
  for num_field in ['declaredIncome', 'requestedLoanAmount', 'existingObligations', 'bankAvgBalance', 'declaredAssets']:
    if num_field in mapped_dict and mapped_dict[num_field] is not None:
      mapped_dict[num_field] = _clean_numeric_str(mapped_dict[num_field])

  # Cast booleans flexibly (supports True/False, Yes/No, 1/0, Y/N)
  for flag in ['writeOffFlag', 'settlementFlag', 'defaultFlag']:
    val = mapped_dict.get(flag)
    if isinstance(val, str):
      mapped_dict[flag] = val.strip().lower() in ('true', 'yes', 'y', '1')
    elif isinstance(val, (int, float)):
      mapped_dict[flag] = bool(val)

  # 2. Missing critical fields check
  missing_fields = find_missing_critical_fields(mapped_dict)
  if missing_fields:
    raise ValueError(f"Essential fields are missing: {', '.join(missing_fields)}")
  
  # 3. Semantic validation
  validation_errors = semantic_validation(mapped_dict)
  
  structured = StructuredRow(**mapped_dict)
  
  profile = NormalizedApplicantProfile(
    **structured.model_dump(),
    sourceType="structured",
    missingFields=missing_fields,
    unmappedFields=unmapped_fields,
    validationErrors=validation_errors
  )
  
  return profile


