from io import BytesIO
import hmac
import json
import os
from pathlib import Path
import re

import streamlit as st
from pypdf import PdfReader

from genAIOverview import generate_overview
from genQuestions import generate_questions
from loadJson import load_measures
from openai_helper import get_client, load_api_key


MEASURE_DETAIL_FIELDS = [
    ("Product Lines", "product_lines"),
    ("Definition", "definition"),
    ("Eligible Population", "eligible_population"),
    ("Continuous Enrollment", "continuous_enrollment"),
    ("Denominator", "denominator"),
    ("Numerator", "numerator"),
    ("Best Practice and Measure Tips", "best_practice_tips"),
    ("Exclusions", "exclusions"),
    ("Measure Codes", "measure_codes"),
    ("Exclusion Codes", "exclusion_codes"),
]

SECTION_LABELS = {
    "product_lines": ["Product Lines:"],
    "definition": ["Definition:", "Description:"],
    "eligible_population": ["Eligible Population:"],
    "continuous_enrollment": ["Continuous Enrollment:", "Continuous Enrollment/Allocation:"],
    "denominator": ["Denominator:"],
    "numerator": ["Numerator Compliance", "Numerator:"],
    "best_practice_tips": ["Best Practice and Measure Tips"],
    "exclusions": ["Measure Exclusions", "Required Exclusions:", "Exclusions:"],
    "measure_codes": ["Measure Codes"],
    "exclusion_codes": ["Exclusion Codes"],
}


@st.cache_data
def get_measures():
    return [normalize_measure_record(measure) for measure in load_measures()]


DEFAULT_QUESTIONS = [
    "What is the intent of this measure?",
    "Who is included in the eligible population for this measure?",
    "What is the denominator for this measure?",
    "What is the numerator for this measure?",
    "What exclusions apply to this measure?",
    "Which measures relate to medication adherence?",
    "Which measures focus on follow-up care?",
    "Which measures appear most relevant for reporting operations?",
]

MEASURE_PLACEHOLDER = "Choose a measure"
UPLOADED_PDF_NAME = "uploaded_hedis_source.pdf"
UPLOADED_JSON_NAME = "hedis_measures.json"

MEASURE_QUESTIONS = [
    "What is the intent of this measure?",
    "Who is included in the eligible population for this measure?",
    "What is the denominator for this measure?",
    "What is the numerator for this measure?",
    "What exclusions apply to this measure?",
]


DATASET_QUESTIONS = [
    "Which measures relate to medication adherence?",
    "Which measures focus on follow-up care?",
    "Which measures appear most relevant for reporting operations?",
    "Which measures mention behavioral health or mental health?",
    "Which measures involve preventive screening or wellness care?",
]

USER_AUTH_USERNAME_KEY = "APP_USER_USERNAME"
USER_AUTH_PASSWORD_KEY = "APP_USER_PASSWORD"
ADMIN_AUTH_PASSWORD_KEY = "APP_ADMIN_PASSWORD"


def tokenize(text):
    return re.findall(r"[a-z0-9]+", str(text).lower())


def get_app_secret(name, base_dir=None):
    env_value = os.environ.get(name)
    if env_value:
        return env_value

    try:
        secret_value = st.secrets.get(name)
    except Exception:
        secret_value = None
    if secret_value:
        os.environ[name] = secret_value
        return secret_value

    if base_dir is None:
        return None

    env_path = base_dir / ".env"
    if not env_path.exists():
        return None

    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{name}="):
            _, raw_value = line.split("=", 1)
            cleaned = raw_value.strip().strip("\"'“”")
            if cleaned:
                os.environ[name] = cleaned
                return cleaned

    return None


def user_auth_configured(base_dir):
    return bool(get_app_secret(USER_AUTH_USERNAME_KEY, base_dir)) and bool(
        get_app_secret(USER_AUTH_PASSWORD_KEY, base_dir)
    )


def admin_auth_configured(base_dir):
    return bool(get_app_secret(ADMIN_AUTH_PASSWORD_KEY, base_dir))


def credentials_match(expected_value, candidate_value):
    return bool(expected_value) and hmac.compare_digest(
        str(expected_value), str(candidate_value or "")
    )


def login_shared_user(base_dir):
    expected_username = get_app_secret(USER_AUTH_USERNAME_KEY, base_dir)
    expected_password = get_app_secret(USER_AUTH_PASSWORD_KEY, base_dir)
    entered_username = st.session_state.get("login_username", "").strip()
    entered_password = st.session_state.get("login_password", "")

    if credentials_match(expected_username, entered_username) and credentials_match(
        expected_password, entered_password
    ):
        st.session_state["is_user_authenticated"] = True
        st.session_state["login_error"] = ""
        st.session_state["login_password"] = ""
        return

    st.session_state["is_user_authenticated"] = False
    st.session_state["is_admin_authenticated"] = False
    st.session_state["login_error"] = "Invalid shared username or password."


def login_admin(base_dir):
    expected_password = get_app_secret(ADMIN_AUTH_PASSWORD_KEY, base_dir)
    entered_password = st.session_state.get("admin_password", "")

    if credentials_match(expected_password, entered_password):
        st.session_state["is_admin_authenticated"] = True
        st.session_state["admin_login_error"] = ""
        st.session_state["admin_password"] = ""
        return

    st.session_state["is_admin_authenticated"] = False
    st.session_state["admin_login_error"] = "Invalid admin password."


def logout_shared_user():
    st.session_state["is_user_authenticated"] = False
    st.session_state["is_admin_authenticated"] = False
    st.session_state["current_view"] = "Home"
    st.session_state["login_username"] = ""
    st.session_state["login_password"] = ""


def logout_admin():
    st.session_state["is_admin_authenticated"] = False
    st.session_state["admin_password"] = ""


def clean_inline_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_labeled_section(text, start_labels, all_labels):
    working_text = clean_inline_text(text)
    if not working_text:
        return ""

    start_index = -1
    matched_label = ""
    for label in start_labels:
        idx = working_text.lower().find(label.lower())
        if idx != -1 and (start_index == -1 or idx < start_index):
            start_index = idx
            matched_label = label

    if start_index == -1:
        return ""

    section_start = start_index + len(matched_label)
    section_end = len(working_text)
    for label in all_labels:
        if label.lower() == matched_label.lower():
            continue
        idx = working_text.lower().find(label.lower(), section_start)
        if idx != -1 and idx < section_end:
            section_end = idx

    return clean_inline_text(working_text[section_start:section_end]).strip(":- ")


def normalize_measure_record(measure):
    normalized = dict(measure)
    raw_measure_name = clean_inline_text(normalized.get("measure_name", ""))
    title_product_lines = ""
    if "Product Lines:" in raw_measure_name:
        raw_measure_name, title_product_lines = raw_measure_name.split("Product Lines:", 1)
        raw_measure_name = clean_inline_text(raw_measure_name)
        title_product_lines = clean_inline_text(title_product_lines)

    normalized["measure_name"] = raw_measure_name
    all_labels = [label for labels in SECTION_LABELS.values() for label in labels]
    source_text = " ".join(
        clean_inline_text(measure.get(key, ""))
        for key in [
            "measure_name",
            "description",
            "eligible_population",
            "denominator",
            "numerator",
            "exclusions",
            "product_lines",
        ]
    )

    normalized.setdefault("description", "")
    normalized.setdefault("product_lines", "")
    normalized.setdefault("eligible_population", "")
    normalized.setdefault("denominator", "")
    normalized.setdefault("numerator", "")
    normalized.setdefault("exclusions", "")
    normalized["product_lines"] = clean_inline_text(
        normalized.get("product_lines")
        or title_product_lines
        or extract_labeled_section(source_text, SECTION_LABELS["product_lines"], all_labels)
    )
    normalized["definition"] = clean_inline_text(
        normalized.get("definition")
        or extract_labeled_section(source_text, SECTION_LABELS["definition"], all_labels)
        or normalized.get("description", "")
    )
    normalized["continuous_enrollment"] = clean_inline_text(
        normalized.get("continuous_enrollment")
        or extract_labeled_section(
            source_text, SECTION_LABELS["continuous_enrollment"], all_labels
        )
    )
    normalized["best_practice_tips"] = clean_inline_text(
        normalized.get("best_practice_tips")
        or extract_labeled_section(source_text, SECTION_LABELS["best_practice_tips"], all_labels)
    )
    normalized["measure_codes"] = clean_inline_text(
        normalized.get("measure_codes")
        or extract_labeled_section(source_text, SECTION_LABELS["measure_codes"], all_labels)
    )
    normalized["exclusion_codes"] = clean_inline_text(
        normalized.get("exclusion_codes")
        or extract_labeled_section(source_text, SECTION_LABELS["exclusion_codes"], all_labels)
    )
    return normalized


def keyword_score(query, document_text):
    query_tokens = tokenize(query)
    document_tokens = tokenize(document_text)

    if not query_tokens or not document_tokens:
        return 0.0

    query_counts = {}
    for token in query_tokens:
        query_counts[token] = query_counts.get(token, 0) + 1

    document_counts = {}
    for token in document_tokens:
        document_counts[token] = document_counts.get(token, 0) + 1

    overlap = 0.0
    for token, count in query_counts.items():
        overlap += min(count, document_counts.get(token, 0))

    return overlap / max(len(query_tokens), 1)


def build_measure_context(measure):
    measure = normalize_measure_record(measure)
    return f"""
Measure Name:
{measure.get("measure_name", "")}

Description:
{measure.get("description", "")}

Definition:
{measure.get("definition", "")}

Eligible Population:
{measure.get("eligible_population", "")}

Continuous Enrollment:
{measure.get("continuous_enrollment", "")}

Denominator:
{measure.get("denominator", "")}

Numerator:
{measure.get("numerator", "")}

Best Practice and Measure Tips:
{measure.get("best_practice_tips", "")}

Exclusions:
{measure.get("exclusions", "")}

Measure Codes:
{measure.get("measure_codes", "")}

Exclusion Codes:
{measure.get("exclusion_codes", "")}

Pages:
{measure.get("pages", [])}
""".strip()


def build_searchable_text(measure):
    measure = normalize_measure_record(measure)
    return "\n".join(
        [
            str(measure.get("measure_name", "")),
            str(measure.get("description", "")),
            str(measure.get("definition", "")),
            str(measure.get("eligible_population", "")),
            str(measure.get("continuous_enrollment", "")),
            str(measure.get("denominator", "")),
            str(measure.get("numerator", "")),
            str(measure.get("best_practice_tips", "")),
            str(measure.get("exclusions", "")),
            str(measure.get("measure_codes", "")),
            str(measure.get("exclusion_codes", "")),
        ]
    )


def expand_search_terms(raw_query):
    base_terms = [term for term in tokenize(raw_query) if term]
    expanded_terms = set(base_terms)

    for term in base_terms:
        if term.endswith("ics") and len(term) > 4:
            expanded_terms.add(term[:-1])
        if term.endswith("es") and len(term) > 3:
            expanded_terms.add(term[:-2])
        if term.endswith("s") and len(term) > 3:
            expanded_terms.add(term[:-1])

    return expanded_terms


def measure_matches_search(measure, raw_query):
    searchable_text = build_searchable_text(measure).lower()
    expanded_terms = expand_search_terms(raw_query)
    return any(term in searchable_text for term in expanded_terms)


def format_measure_label(name):
    if name == MEASURE_PLACEHOLDER:
        return name

    normalized = str(name).strip()
    match = re.match(r"^([A-Z0-9-]+)\s+[—-]\s+(.+)$", normalized)
    if match:
        code, remainder = match.groups()
        return f"{code} | {remainder}"

    return normalized


def get_measure_abbreviation(name):
    normalized = str(name).strip()
    match = re.match(r"^([A-Z0-9-]+)\s+[—-]\s+(.+)$", normalized)
    if match:
        return match.group(1)
    return normalized.split()[0] if normalized else "Selected"


def sanitize_text_for_api(text):
    cleaned = str(text).replace("\x00", " ")
    cleaned = re.sub(r"[\x01-\x08\x0B\x0C\x0E-\x1F]", " ", cleaned)
    cleaned = cleaned.encode("utf-8", "replace").decode("utf-8")
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_pages_from_pdf_bytes(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = []

    for index, page in enumerate(reader.pages, start=1):
        page_text = sanitize_text_for_api(page.extract_text() or "")
        pages.append({"page_number": index, "text": page_text})

    return pages


def chunk_pdf_pages(pages, max_chars=18000):
    chunks = []
    current_chunk = []
    current_size = 0

    for page in pages:
        page_block = f"Page {page['page_number']}:\n{page['text']}\n"
        block_size = len(page_block)

        if current_chunk and current_size + block_size > max_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0

        current_chunk.append(page)
        current_size += block_size

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def strip_json_fences(raw_text):
    cleaned = str(raw_text).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def clean_text(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def is_measure_header(text):
    if not text:
        return False

    text = text.strip()
    pattern1 = r"^[A-Z][A-Za-z0-9 ,'\-\/]+ \([A-Z0-9-]{2,}\)$"
    pattern2 = r"^[A-Z][A-Z0-9-]{1,10}\s*[—-]\s*.+$"
    return bool(re.match(pattern1, text) or re.match(pattern2, text))


def extract_measures_offline_from_pages(pages):
    measures = []
    current_measure = None

    for page in pages:
        page_number = page["page_number"]
        text = page["text"]
        if not text:
            continue

        lines = [line.strip() for line in text.splitlines()]
        index = 0

        while index < len(lines):
            line = lines[index]
            if not line:
                index += 1
                continue

            lookahead = " ".join(
                candidate for candidate in lines[index + 1 : index + 8] if candidate
            )
            has_section_markers = (
                "Product Lines:" in lookahead
                or "Eligible Population:" in lookahead
                or "Definition:" in lookahead
            )

            if is_measure_header(line) and has_section_markers:
                title_parts = [line]
                next_index = index + 1

                while next_index < len(lines):
                    candidate = lines[next_index]
                    if not candidate:
                        next_index += 1
                        continue
                    if candidate in {
                        "Product Lines:",
                        "Eligible Population:",
                        "Definition:",
                        "Numerator",
                        "Denominator",
                    }:
                        break
                    title_parts.append(candidate)
                    next_index += 1

                measure_name = clean_text(" ".join(title_parts))

                if current_measure:
                    measures.append(current_measure)

                current_measure = {
                    "measure_name": measure_name,
                    "content": "",
                    "tables": [],
                    "pages": [page_number],
                }
                index = next_index
                continue

            if current_measure:
                current_measure["content"] += line + "\n"
                if page_number not in current_measure["pages"]:
                    current_measure["pages"].append(page_number)

            index += 1

    if current_measure:
        measures.append(current_measure)

    return measures


def structure_measure_offline(measure):
    content = measure["content"]
    sections = {
        "description": "",
        "product_lines": "",
        "definition": "",
        "eligible_population": "",
        "continuous_enrollment": "",
        "numerator": "",
        "denominator": "",
        "best_practice_tips": "",
        "exclusions": "",
        "measure_codes": "",
        "exclusion_codes": "",
    }
    current_section = None

    for line in content.splitlines():
        lowered = line.lower()

        if "definition" in lowered:
            current_section = "definition"
            continue
        if "description" in lowered:
            current_section = "description"
            continue
        if "product lines" in lowered:
            current_section = "product_lines"
            continue
        if "eligible population" in lowered:
            current_section = "eligible_population"
            continue
        if "continuous enrollment" in lowered:
            current_section = "continuous_enrollment"
            continue
        if "numerator" in lowered:
            current_section = "numerator"
            continue
        if "denominator" in lowered:
            current_section = "denominator"
            continue
        if "best practice and measure tips" in lowered:
            current_section = "best_practice_tips"
            continue
        if "exclusion" in lowered:
            current_section = "exclusions"
            continue
        if "measure codes" in lowered:
            current_section = "measure_codes"
            continue
        if "exclusion codes" in lowered:
            current_section = "exclusion_codes"
            continue
        if current_section:
            sections[current_section] += line + " "

    return normalize_measure_record(
        {
        "measure_name": measure["measure_name"],
        "pages": measure["pages"],
        "description": clean_text(sections["description"]),
        "product_lines": clean_text(sections["product_lines"]),
        "definition": clean_text(sections["definition"]),
        "eligible_population": clean_text(sections["eligible_population"]),
        "continuous_enrollment": clean_text(sections["continuous_enrollment"]),
        "numerator": clean_text(sections["numerator"]),
        "denominator": clean_text(sections["denominator"]),
        "best_practice_tips": clean_text(sections["best_practice_tips"]),
        "exclusions": clean_text(sections["exclusions"]),
        "measure_codes": clean_text(sections["measure_codes"]),
        "exclusion_codes": clean_text(sections["exclusion_codes"]),
        "tables": measure["tables"],
        }
    )


def merge_measure_records(records):
    merged = {}

    for record in records:
        measure_name = str(record.get("measure_name", "")).strip()
        if not measure_name:
            continue

        existing = merged.setdefault(
            measure_name,
            {
                "measure_name": measure_name,
                "pages": [],
                "description": "",
                "product_lines": "",
                "definition": "",
                "eligible_population": "",
                "continuous_enrollment": "",
                "denominator": "",
                "numerator": "",
                "best_practice_tips": "",
                "exclusions": "",
                "measure_codes": "",
                "exclusion_codes": "",
                "tables": [],
            },
        )

        incoming_pages = record.get("pages", [])
        if isinstance(incoming_pages, list):
            existing["pages"] = sorted(
                {int(page) for page in existing["pages"] + incoming_pages if str(page).isdigit()}
            )

        for field in [
            "description",
            "product_lines",
            "definition",
            "eligible_population",
            "continuous_enrollment",
            "denominator",
            "numerator",
            "best_practice_tips",
            "exclusions",
            "measure_codes",
            "exclusion_codes",
        ]:
            incoming_value = str(record.get(field, "")).strip()
            if len(incoming_value) > len(existing.get(field, "").strip()):
                existing[field] = incoming_value

        incoming_tables = record.get("tables", [])
        if isinstance(incoming_tables, list):
            for table in incoming_tables:
                if table not in existing["tables"]:
                    existing["tables"].append(table)

    return list(merged.values())


def convert_pdf_chunk_to_records(chunk_pages):
    client = get_client()
    chunk_text = "\n\n".join(
        f"Page {page['page_number']}:\n{page['text']}" for page in chunk_pages
    )
    prompt = sanitize_text_for_api(
        f"""
You are extracting HEDIS measures from a PDF text chunk.
Return JSON only.

Produce a JSON array. Each item must be an object with exactly these keys:
- measure_name
- pages
- description
- product_lines
- definition
- eligible_population
- continuous_enrollment
- denominator
- numerator
- best_practice_tips
- exclusions
- measure_codes
- exclusion_codes
- tables

Rules:
- Include only actual HEDIS measure entries that are present in this chunk.
- Use the exact measure title when possible, including abbreviation if shown.
- "pages" must be a list of integers.
- If a field is not available, use an empty string.
- "tables" must be a JSON array. Use [] when no table content is available.
- Do not include prose before or after the JSON.

PDF text chunk:
{chunk_text}
"""
    )

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    content = response.choices[0].message.content
    parsed = json.loads(strip_json_fences(content))
    return parsed if isinstance(parsed, list) else []


def convert_pdf_bytes_to_measures(pdf_bytes, progress_callback=None):
    pages = extract_pages_from_pdf_bytes(pdf_bytes)
    if progress_callback:
        progress_callback(0.15, f"Read {len(pages)} page(s) from the PDF.")

    non_empty_pages = [page for page in pages if page["text"].strip()]
    if not non_empty_pages:
        raise ValueError("No readable text was found in the uploaded PDF.")

    if progress_callback:
        progress_callback(0.25, "Trying fast local PDF parsing first...")
    offline_raw_measures = extract_measures_offline_from_pages(non_empty_pages)
    offline_measures = [structure_measure_offline(measure) for measure in offline_raw_measures]
    offline_measures = [
        measure for measure in offline_measures if measure.get("measure_name", "").strip()
    ]
    if offline_measures:
        offline_measures.sort(key=lambda item: item.get("measure_name", "").lower())
        if progress_callback:
            progress_callback(
                0.95,
                f"Fast local parsing found {len(offline_measures)} measure record(s).",
            )
        return offline_measures, len(pages)

    chunks = chunk_pdf_pages(non_empty_pages)
    if progress_callback:
        progress_callback(
            0.25,
            f"Fast parsing found no measures. Falling back to AI across {len(chunks)} chunk(s).",
        )

    all_records = []
    total_chunks = max(len(chunks), 1)
    for index, chunk in enumerate(chunks, start=1):
        all_records.extend(convert_pdf_chunk_to_records(chunk))
        if progress_callback:
            progress_value = 0.25 + (0.55 * index / total_chunks)
            progress_callback(
                progress_value,
                f"Converted chunk {index} of {total_chunks} to structured measure records.",
            )

    merged_records = merge_measure_records(all_records)
    if not merged_records:
        raise ValueError("The PDF was read, but no measure records could be extracted.")

    merged_records.sort(key=lambda item: item.get("measure_name", "").lower())
    if progress_callback:
        progress_callback(
            0.9,
            f"Merged extracted content into {len(merged_records)} measure record(s).",
        )
    return merged_records, len(pages)


def save_uploaded_dataset(base_dir, pdf_bytes, measures):
    pdf_path = base_dir / UPLOADED_PDF_NAME
    json_path = base_dir / UPLOADED_JSON_NAME
    pdf_path.write_bytes(pdf_bytes)
    json_path.write_text(json.dumps(measures, indent=2), encoding="utf-8")
    return pdf_path, json_path


def ask_measure_question(measure, question):
    client = get_client()
    context = build_measure_context(measure)
    prompt = f"""
You are a helpful HEDIS assistant.
Answer the user's question using only the measure information below.
If the answer is not clearly available in the context, say that clearly.

Context:
{context}

Question:
{question}
"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def build_dataset_context(measures, max_measures=12):
    selected_measures = measures[:max_measures]
    sections = []

    for measure in selected_measures:
        measure = normalize_measure_record(measure)
        sections.append(
            f"""
Measure Name: {measure.get("measure_name", "")}
Description: {measure.get("description", "")}
Definition: {measure.get("definition", "")}
Eligible Population: {measure.get("eligible_population", "")}
Continuous Enrollment: {measure.get("continuous_enrollment", "")}
Denominator: {measure.get("denominator", "")}
Numerator: {measure.get("numerator", "")}
Best Practice and Measure Tips: {measure.get("best_practice_tips", "")}
Exclusions: {measure.get("exclusions", "")}
Measure Codes: {measure.get("measure_codes", "")}
Exclusion Codes: {measure.get("exclusion_codes", "")}
""".strip()
        )

    return "\n\n".join(sections)


def ask_dataset_question(measures, question):
    client = get_client()
    context = build_dataset_context(measures)
    prompt = f"""
You are a helpful HEDIS assistant.
Answer the user's question using only the dataset context below.
If the answer is not clearly available in the context, say that clearly.

Dataset size: {len(measures)} measures total.
Context sample:
{context}

Question:
{question}
"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def search_measures(measures, query, k=None):
    ranked = []

    for measure in measures:
        searchable_text = build_searchable_text(measure)
        score = keyword_score(query, searchable_text)
        if score > 0:
            ranked.append((score, measure))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[:k] if k is not None else ranked


def is_measure_listing_query(question):
    normalized = str(question).lower()
    patterns = [
        "what measures",
        "which measures",
        "list measures",
        "show measures",
        "measures related to",
        "measures for",
    ]
    return any(pattern in normalized for pattern in patterns)


def select_relevant_results(ranked_results, max_results=20, min_absolute=0.04, ratio=0.35):
    if not ranked_results:
        return []

    top_score = ranked_results[0][0]
    cutoff = max(min_absolute, top_score * ratio)
    selected = [item for item in ranked_results if item[0] >= cutoff]
    return selected[:max_results]


def build_measure_list_answer(question, ranked_results):
    lines = [
        f"I found {len(ranked_results)} relevant measure(s) for: {question}",
        "",
    ]
    for _, measure in ranked_results:
        lines.append(f"- {measure.get('measure_name', 'Unknown Measure')}")
    return "\n".join(lines)


def answer_from_local_results(question, ranked_results, context_limit=8):
    client = get_client()
    context_blocks = []

    for score, measure in ranked_results[:context_limit]:
        context_blocks.append(
            f"""
Match score: {score:.2f}
{build_measure_context(measure)}
""".strip()
        )

    prompt = f"""
You are a helpful HEDIS assistant.
Answer the user's question using only the retrieved HEDIS dataset context below.
If the answer is not clearly supported by the context, say that clearly.

Retrieved context:
{chr(10).join(context_blocks)}

Question:
{question}
"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def answer_with_external_fallback(question):
    client = get_client()
    prompt = f"""
You are a helpful HEDIS assistant.
The local HEDIS dataset did not clearly answer the question below.
Provide a best-effort general answer using your broader knowledge.
Clearly state that this answer is not grounded in the local dataset.
Do not claim you found support in the dataset.

Question:
{question}
"""

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def hybrid_answer(measures, question, local_threshold=0.08):
    ranked_results = search_measures(measures, question)
    relevant_results = select_relevant_results(ranked_results)

    if relevant_results and relevant_results[0][0] >= local_threshold:
        if is_measure_listing_query(question):
            answer = build_measure_list_answer(question, relevant_results)
        else:
            answer = answer_from_local_results(question, relevant_results)
        return {
            "mode": "local_dataset",
            "answer": answer,
            "results": relevant_results,
        }

    answer = answer_with_external_fallback(question)
    return {
        "mode": "external_fallback",
        "answer": answer,
        "results": relevant_results,
    }


def answer_measure_with_fallback(measure, measures, question):
    local_answer = ask_measure_question(measure, question)
    normalized = local_answer.lower()

    fallback_markers = [
        "not clearly available in the context",
        "not clearly supported by the context",
        "cannot determine from the provided context",
        "can't determine from the provided context",
        "not provided in the context",
    ]

    if any(marker in normalized for marker in fallback_markers):
        dataset_result = hybrid_answer(measures, question)
        dataset_result["mode"] = f"measure_to_{dataset_result['mode']}"
        return dataset_result

    return {
        "mode": "selected_measure",
        "answer": local_answer,
        "results": [(1.0, measure)],
    }


def parse_question_list(raw_text):
    questions = []
    for line in raw_text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue

        if "." in cleaned:
            prefix, remainder = cleaned.split(".", 1)
            if prefix.strip().isdigit() and remainder.strip():
                cleaned = remainder.strip()

        if cleaned.startswith(("-", "*")):
            cleaned = cleaned[1:].strip()

        if cleaned:
            questions.append(cleaned)

    return questions


@st.cache_data(show_spinner=False)
def get_suggested_questions(measures):
    try:
        return parse_question_list(generate_questions(measures))
    except Exception:
        return DEFAULT_QUESTIONS


def render_measure_details(measure):
    measure = normalize_measure_record(measure)
    fields = MEASURE_DETAIL_FIELDS

    for label, key in fields:
        st.markdown(f'<div class="detail-label">{label}</div>', unsafe_allow_html=True)
        value = measure.get(key, "")
        st.markdown(format_measure_detail_as_bullets(value))


def render_search_results(ranked_results):
    if not ranked_results:
        st.caption("No strong local dataset matches were found.")
        return

    with st.expander(f"Local dataset matches ({len(ranked_results)})"):
        for index, (score, measure) in enumerate(ranked_results, start=1):
            st.markdown(
                f"**Match {index}: {measure.get('measure_name', 'Unknown Measure')}**"
            )
            st.caption(f"Score: {score:.2f}")
            st.write(measure.get("description", "No description available."))


def format_answer_as_bullets(answer):
    lines = [line.strip() for line in str(answer).splitlines() if line.strip()]
    if not lines:
        return "- No answer returned."

    if any(
        line.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. "))
        for line in lines
    ):
        return "\n".join(lines)

    chunks = []
    for line in lines:
        sentences = re.split(r"(?<=[.!?])\s+", line)
        for sentence in sentences:
            cleaned = sentence.strip()
            if cleaned:
                chunks.append(f"- {cleaned}")

    return "\n".join(chunks) if chunks else "- No answer returned."


def format_measure_detail_as_bullets(value):
    text = str(value or "").strip()
    if not text:
        return "Not available."

    normalized = (
        text.replace("•", "\n• ")
        .replace("`", "\n• ")
        .replace("»", "\n• ")
    )
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    bullets = []

    for line in lines:
        cleaned = re.sub(r"^\d+\.\s*", "", line).strip()
        cleaned = re.sub(r"^[-*•]+\s*", "", cleaned).strip()
        if cleaned:
            bullets.append(f"- {cleaned}")

    if bullets:
        return "\n".join(bullets)

    sentences = re.split(r"(?<=[.!?])\s+", text)
    bullets = [f"- {sentence.strip()}" for sentence in sentences if sentence.strip()]
    return "\n".join(bullets) if bullets else "Not available."


def use_selected_measure_question():
    st.session_state["custom_measure_question"] = ""


def use_selected_dataset_question():
    st.session_state["custom_dataset_question"] = ""
    st.session_state.pop("dataset_result", None)


def request_overview_generation():
    st.session_state["current_view"] = "Overview"
    st.session_state["generate_overview_requested"] = True


def handle_measure_change():
    st.session_state.pop("measure_result", None)
    st.session_state["custom_measure_question"] = ""


def handle_dataset_question_change():
    st.session_state.pop("dataset_result", None)


def apply_measure_search():
    st.session_state["applied_measure_search"] = st.session_state.get(
        "measure_search_input", ""
    )


def clear_measure_search():
    st.session_state["measure_search_input"] = ""
    st.session_state["applied_measure_search"] = ""
    st.session_state["selected_measure_name"] = MEASURE_PLACEHOLDER
    handle_measure_change()


def handle_pdf_upload(base_dir, uploaded_file):
    pdf_bytes = uploaded_file.getvalue()
    file_name = uploaded_file.name
    progress_bar = st.progress(0, text=f"Starting PDF conversion for {file_name}...")
    status_placeholder = st.empty()

    def update_progress(value, message):
        progress_bar.progress(int(value * 100), text=message)
        status_placeholder.caption(message)

    try:
        update_progress(0.05, f"Processing {file_name}...")
        measures, page_count = convert_pdf_bytes_to_measures(
            pdf_bytes, progress_callback=update_progress
        )
        update_progress(0.95, f"Saving converted JSON for {file_name}...")
        pdf_path, json_path = save_uploaded_dataset(base_dir, pdf_bytes, measures)
        update_progress(1.0, f"PDF conversion completed for {file_name}.")
    finally:
        status_placeholder.empty()

    get_measures.clear()
    get_suggested_questions.clear()
    st.session_state["pending_measure_reset"] = True
    st.session_state["pending_search_reset"] = True
    st.session_state.pop("measure_result", None)
    st.session_state.pop("dataset_result", None)
    st.session_state.pop("overview_result", None)
    st.session_state.pop("overview_error", None)
    st.session_state["pdf_upload_success"] = (
        f"Uploaded PDF saved to {pdf_path.name} and converted to {json_path.name}. "
        f"Loaded {len(measures)} measure(s) from {page_count} page(s)."
    )
    st.rerun()


def is_pdf_upload(uploaded_file):
    if uploaded_file is None:
        return False

    file_name = (uploaded_file.name or "").lower()
    file_type = (getattr(uploaded_file, "type", "") or "").lower()
    return file_name.endswith(".pdf") or file_type == "application/pdf"


def main():
    base_dir = Path(__file__).resolve().parent
    api_key = load_api_key(base_dir)

    st.set_page_config(
        page_title="HEDIS AI Assistant",
        page_icon="H",
        layout="wide",
    )
    st.markdown(
        """
<style>
.block-container {
    padding-top: 0.35rem;
    padding-bottom: 1rem;
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at top right, rgba(214, 234, 248, 0.6), transparent 26%),
        radial-gradient(circle at top left, rgba(249, 243, 209, 0.55), transparent 24%),
        linear-gradient(180deg, #fffdf8 0%, #f7fafc 100%);
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #fbfcff 0%, #f3f7fb 100%);
    border-right: 1px solid #dfe7f2;
}

[data-testid="stSidebar"] .block-container {
    padding-top: 0.7rem;
}

[data-testid="stHeader"] {
    background: rgba(255, 253, 248, 0.82);
    backdrop-filter: blur(6px);
}

[data-testid="stToolbar"] {
    right: 1rem;
}

[data-testid="stFileUploaderDropzone"] svg {
    display: none;
}

[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] svg {
    display: none;
}

[data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {
    display: none;
}

[data-testid="stFileUploader"] {
    margin-bottom: 0;
}

[data-testid="stFileUploaderDropzone"] {
    min-height: 5.5rem;
    padding: 0.6rem 0.85rem;
}

hr {
    margin: 0.55rem 0 0.7rem 0;
}

.uploaded-file-bar {
    margin-top: 0;
    margin-bottom: 0.25rem;
    padding: 0.55rem 0.75rem;
    border: 1px solid #d7e2ef;
    border-radius: 0.8rem;
    background: linear-gradient(180deg, #fbfdff 0%, #f5f9fd 100%);
    color: #243447;
    font-size: 0.95rem;
    line-height: 1.25;
    min-height: 2.65rem;
    display: flex;
    align-items: center;
}

.uploaded-file-name {
    font-weight: 700;
}

.upload-action-row {
    margin-top: 0;
    margin-bottom: 0;
}

.brand-shell {
    background: linear-gradient(135deg, #ffffff 0%, #f4f8fc 100%);
    border: 1px solid #dde7f1;
    border-radius: 22px;
    padding: 1rem 1.15rem 0.95rem 1.15rem;
    margin-bottom: 0.35rem;
    box-shadow: 0 12px 30px rgba(22, 36, 58, 0.06);
    text-align: center;
}

.brand-kicker {
    font-size: 0.82rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #7b879c;
    margin-bottom: 0.25rem;
    font-weight: 700;
}

.brand-title {
    font-size: 2rem;
    line-height: 1.05;
    font-weight: 800;
    color: #102033;
    margin-bottom: 0;
}

[data-testid="stRadio"] > div {
    background: rgba(255, 255, 255, 0.78);
    border: 1px solid #dee7f0;
    border-radius: 18px;
    padding: 0.35rem 0.4rem;
    box-shadow: 0 8px 24px rgba(23, 34, 51, 0.05);
}

[data-testid="stRadio"] {
    margin-bottom: 0.2rem;
}

[data-testid="stRadio"] label {
    border-radius: 12px;
    padding: 0.2rem 0.2rem;
    color: #1c2736;
}

[data-testid="stRadio"] label p {
    font-weight: 600;
    color: #1c2736;
}

[data-testid="stRadio"] input:checked + div,
[data-testid="stRadio"] label:has(input:checked) {
    background: #e6f0fb;
    border-radius: 12px;
}

[data-testid="stRadio"] label:hover p {
    color: #102033;
}

div[data-testid="stTextInput"] input {
    background-color: #fff7d6;
    color: #1f2937;
    -webkit-text-fill-color: #1f2937;
    caret-color: #1f2937;
}

div[data-testid="stTextInput"] input:focus {
    background-color: #ffe89a;
    color: #111827;
    -webkit-text-fill-color: #111827;
}

div[data-testid="stTextInput"] input::placeholder {
    color: #6b7280;
    -webkit-text-fill-color: #6b7280;
}

div[data-testid="stTextInput"] > div {
    background-color: #fff7d6;
}

div[data-testid="stTextInput"] input:-webkit-autofill,
div[data-testid="stTextInput"] input:-webkit-autofill:hover,
div[data-testid="stTextInput"] input:-webkit-autofill:focus,
div[data-testid="stTextInput"] input:-webkit-autofill:active {
    -webkit-text-fill-color: #1f2937 !important;
    -webkit-box-shadow: 0 0 0px 1000px #fff7d6 inset !important;
    box-shadow: 0 0 0px 1000px #fff7d6 inset !important;
    caret-color: #1f2937 !important;
}

div[data-baseweb="select"] > div {
    background-color: #fff7d6;
    color: #1f2937;
}

div[data-baseweb="select"] span {
    color: #1f2937 !important;
}

div[data-baseweb="select"] input {
    color: #1f2937 !important;
    -webkit-text-fill-color: #1f2937 !important;
}

.question-preview {
    background-color: #e8f1ff;
    border: 1px solid #b8d0ff;
    border-radius: 8px;
    padding: 0.45rem 0.7rem;
    margin: 0.05rem 0 0.25rem 0;
    color: #1f2937;
    font-size: 0.94rem;
    line-height: 1.35;
}

.content-panel {
    background: rgba(255, 255, 255, 0.76);
    border: 1px solid #dfe7f0;
    border-radius: 18px;
    padding: 0.8rem 0.9rem 0.85rem 0.9rem;
}

.compact-heading {
    margin-bottom: 0.25rem;
}

.section-tight {
    margin-top: 0.2rem;
}

.question-preview strong,
.question-preview,
.answer-panel,
.answer-panel p,
.overview-panel,
.overview-panel p,
.search-summary,
.search-summary p,
.sidebar-shell,
.sidebar-shell p,
.home-card,
.home-card p {
    color: #1f2937;
}

.ask-panel {
    background: #fcfcfd;
    border: 1px solid #e6eaf2;
    border-radius: 16px;
    padding: 1rem 1rem 0.6rem 1rem;
    margin-bottom: 1rem;
}

.side-panel {
    background: linear-gradient(180deg, #fffdfa 0%, #f8fbff 100%);
    border: 1px solid #e3e9f3;
    border-radius: 18px;
    padding: 1rem 1rem 0.9rem 1rem;
    margin-bottom: 1rem;
}

.side-panel-title {
    font-size: 1rem;
    font-weight: 700;
    color: #172033;
    margin-bottom: 0.35rem;
}

.side-panel-copy {
    font-size: 0.92rem;
    color: #5a6783;
    margin-bottom: 0.9rem;
}

.answer-panel {
    background: #f8fafc;
    border: 1px solid #dbe4ef;
    border-radius: 16px;
    padding: 0.9rem 1rem;
    margin-top: 0.15rem;
}

.overview-panel {
    background: #f8fafc;
    border: 1px solid #dbe4ef;
    border-radius: 16px;
    padding: 0.9rem 1rem;
    margin-top: 0.5rem;
}

.overview-panel ul,
.overview-panel ol {
    padding-left: 1.5rem;
    margin-left: 0.4rem;
}

.overview-panel li {
    margin-bottom: 0.35rem;
}

.detail-label {
    color: #b58900;
    font-weight: 700;
    font-size: 1.15rem;
    margin-top: 0.6rem;
    margin-bottom: 0.2rem;
}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stText"] {
    color: #1f2937;
}

h1, h2, h3 {
    color: #102033;
}

.home-hero {
    background: linear-gradient(140deg, #11263e 0%, #1f4f78 100%);
    border-radius: 24px;
    padding: 0.95rem 1.05rem;
    color: #f8fbff;
    box-shadow: 0 18px 36px rgba(17, 38, 62, 0.16);
    margin-bottom: 0.55rem;
}

.home-hero-title {
    font-size: 1.45rem;
    font-weight: 800;
    line-height: 1.08;
    margin-bottom: 0.25rem;
}

.home-hero-copy {
    color: rgba(248, 251, 255, 0.88);
    max-width: 48rem;
    font-size: 0.94rem;
    line-height: 1.4;
}

.home-card {
    background: rgba(255, 255, 255, 0.86);
    border: 1px solid #dde7f0;
    border-radius: 18px;
    padding: 0.75rem 0.85rem;
    min-height: 100%;
}

.home-card-title {
    font-size: 1rem;
    font-weight: 700;
    color: #172033;
    margin-bottom: 0.25rem;
}

.home-card-copy {
    color: #59677d;
    line-height: 1.35;
    font-size: 0.93rem;
}

.sidebar-shell {
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid #dbe5ef;
    border-radius: 18px;
    padding: 0.95rem 0.95rem 0.85rem 0.95rem;
    margin-bottom: 0.9rem;
}

.sidebar-title {
    font-size: 1rem;
    font-weight: 800;
    color: #162236;
    margin-bottom: 0.25rem;
}

.sidebar-copy {
    font-size: 0.9rem;
    color: #5b6980;
    line-height: 1.45;
}

.search-summary {
    background: #eef6ff;
    border: 1px solid #d2e3f7;
    border-radius: 14px;
    padding: 0.55rem 0.7rem;
    margin: 0.45rem 0 0.55rem 0;
}

.search-summary-title {
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
    color: #5b6d84;
    margin-bottom: 0.2rem;
}

.search-summary-copy {
    color: #172033;
    font-size: 0.95rem;
    line-height: 1.45;
}

div[data-testid="stButton"] > button[kind="secondary"] {
    background: #1f9d55;
    border: 1px solid #168045;
    color: #ffffff;
    font-weight: 700;
}

div[data-testid="stButton"] > button[kind="secondary"]:hover {
    background: #168045;
    color: #ffffff;
}
</style>
""",
        unsafe_allow_html=True,
    )
    st.markdown(
        """
<div class="brand-shell">
    <div class="brand-kicker">Quality Measure Explorer</div>
    <div class="brand-title">HEDIS AI Assistant</div>
</div>
""",
        unsafe_allow_html=True,
    )

    measures = get_measures()
    generated_questions = get_suggested_questions(measures)
    measure_questions = list(dict.fromkeys(MEASURE_QUESTIONS + generated_questions))
    dataset_questions = list(dict.fromkeys(DATASET_QUESTIONS + generated_questions))
    shared_user_auth_ready = user_auth_configured(base_dir)
    admin_auth_ready = admin_auth_configured(base_dir)
    measure_names = sorted(
        [measure.get("measure_name", "Unknown Measure") for measure in measures],
        key=lambda name: name.lower(),
    )
    current_measure_name_for_menu = st.session_state.get(
        "selected_measure_name", MEASURE_PLACEHOLDER
    )
    current_measure_abbreviation = (
        get_measure_abbreviation(current_measure_name_for_menu)
        if current_measure_name_for_menu != MEASURE_PLACEHOLDER
        else "Selected"
    )
    measure_details_menu_label = f"{current_measure_abbreviation} - Measure Details"
    ask_measure_menu_label = f"Ask About {current_measure_abbreviation} Measure"
    view_options = [
        "Home",
        "Overview",
        measure_details_menu_label,
        ask_measure_menu_label,
        "Ask About All Measures",
    ]

    if "measure_question_select" not in st.session_state and measure_questions:
        st.session_state["measure_question_select"] = measure_questions[0]
    if "dataset_question_select" not in st.session_state and dataset_questions:
        st.session_state["dataset_question_select"] = dataset_questions[0]
    if "current_view" not in st.session_state:
        st.session_state["current_view"] = "Home"
    if "is_user_authenticated" not in st.session_state:
        st.session_state["is_user_authenticated"] = False
    if "is_admin_authenticated" not in st.session_state:
        st.session_state["is_admin_authenticated"] = False
    if "login_error" not in st.session_state:
        st.session_state["login_error"] = ""
    if "admin_login_error" not in st.session_state:
        st.session_state["admin_login_error"] = ""
    if "applied_measure_search" not in st.session_state:
        st.session_state["applied_measure_search"] = ""
    if "measure_search_input" not in st.session_state:
        st.session_state["measure_search_input"] = ""
    if not shared_user_auth_ready:
        st.session_state["is_user_authenticated"] = True
    if not admin_auth_ready:
        st.session_state["is_admin_authenticated"] = False
    if st.session_state.pop("pending_search_reset", False):
        st.session_state["measure_search_input"] = ""
        st.session_state["applied_measure_search"] = ""
    if st.session_state.pop("pending_measure_reset", False):
        st.session_state["selected_measure_name"] = MEASURE_PLACEHOLDER
    if st.session_state.get("current_view") == "Ask About This Measure":
        st.session_state["current_view"] = ask_measure_menu_label
    if st.session_state.get("current_view") == "Measure Details":
        st.session_state["current_view"] = measure_details_menu_label
    accessible_view_options = view_options if st.session_state["is_user_authenticated"] else ["Home"]
    if st.session_state.get("current_view", "") not in accessible_view_options:
        st.session_state["current_view"] = "Home"

    current_view = st.radio(
        "Main Menu",
        accessible_view_options,
        key="current_view",
        horizontal=True,
        label_visibility="collapsed",
    )

    with st.sidebar:
        st.markdown(
            """
<div class="sidebar-shell">
    <div class="sidebar-title">Control Panel</div>
    <div class="sidebar-copy">
        Access and navigation
    </div>
</div>
""",
            unsafe_allow_html=True,
        )
        if shared_user_auth_ready:
            if not st.session_state["is_user_authenticated"]:
                st.caption("Sign in with the shared user credentials to unlock the app.")
                st.text_input("Username", key="login_username")
                st.text_input("Password", type="password", key="login_password")
                st.button(
                    "Sign In",
                    use_container_width=True,
                    on_click=login_shared_user,
                    args=(base_dir,),
                )
                if st.session_state.get("login_error"):
                    st.error(st.session_state["login_error"])
            else:
                st.button("Sign Out", use_container_width=True, on_click=logout_shared_user)
        else:
            st.info("Shared user login is not configured, so full navigation stays open locally.")

        if st.session_state["is_user_authenticated"]:
            st.markdown(
                """
<div class="sidebar-shell">
    <div class="sidebar-title">Measure Search</div>
    <div class="sidebar-copy">
        SEARCH / CHOOSE measure
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.text_input(
                "Filter measures by name",
                placeholder="Type part of a measure name",
                key="measure_search_input",
            )
            search_col, clear_col = st.columns(2)
            search_col.button("Search", use_container_width=True, on_click=apply_measure_search)
            clear_col.button("Clear", use_container_width=True, on_click=clear_measure_search)

            search_value = st.session_state.get("applied_measure_search", "").strip().lower()
            show_all_requested = search_value in {
                "all",
                "show all",
                "list all",
                "list all measures",
                "show all measures",
            }

            filtered_names = (
                sorted(
                    [
                        measure.get("measure_name", "Unknown Measure")
                        for measure in measures
                        if measure_matches_search(measure, search_value)
                    ],
                    key=lambda name: name.lower(),
                )
                if search_value and not show_all_requested
                else measure_names
            )

            if search_value and not filtered_names:
                st.warning("No measures matched that search. Showing all measures instead.")
                filtered_names = measure_names

            if not filtered_names:
                filtered_names = measure_names

            if search_value and not show_all_requested:
                st.markdown(
                    f"""
<div class="search-summary">
    <div class="search-summary-copy">
        <strong>{len(filtered_names)}</strong> matching measure(s) for
        <strong>"{st.session_state.get("applied_measure_search", "").strip()}"</strong>
    </div>
</div>
""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""
<div class="search-summary">
    <div class="search-summary-copy">
        <strong>{len(measure_names)}</strong> available measures
    </div>
</div>
""",
                    unsafe_allow_html=True,
                )

            select_options = [MEASURE_PLACEHOLDER] + filtered_names
            current_selected = st.session_state.get("selected_measure_name")
            if current_selected not in select_options:
                st.session_state["selected_measure_name"] = MEASURE_PLACEHOLDER

            selected_measure_name = st.selectbox(
                "Select a measure",
                select_options,
                key="selected_measure_name",
                on_change=handle_measure_change,
                format_func=format_measure_label,
            )

            if not api_key:
                st.warning("OpenAI API key not found. AI actions will be disabled.")
        else:
            selected_measure_name = MEASURE_PLACEHOLDER

    selected_measure = next(
        (
            measure
            for measure in measures
            if measure.get("measure_name") == selected_measure_name
        ),
        None,
    )

    if current_view == "Home":
        st.markdown(
            """
<div class="home-hero">
    <div class="home-hero-title">Turn HEDIS content into faster answers.</div>
    <div class="home-hero-copy">
        This workspace combines structured HEDIS measure content with AI-assisted exploration,
        helping teams move from manual lookup to guided analysis and targeted Q&A.
    </div>
</div>
""",
            unsafe_allow_html=True,
        )
        if shared_user_auth_ready and not st.session_state["is_user_authenticated"]:
            st.info("Home stays open, but the rest of the app unlocks only after shared user sign-in.")
        col1, col2, col3 = st.columns(3, gap="medium")
        with col1:
            st.markdown(
                """
<div class="home-card">
    <div class="home-card-title">Explore Measures</div>
    <div class="home-card-copy">
        Use the left control panel to search measures by topic or code, then open Measure Details
        to review denominators, numerators, exclusions, and supporting text.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                """
<div class="home-card">
    <div class="home-card-title">Generate Summaries</div>
    <div class="home-card-copy">
        Open Overview to create a concise executive summary of the HEDIS dataset for demos,
        stakeholder reviews, and quick briefings.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
        with col3:
            st.markdown(
                """
<div class="home-card">
    <div class="home-card-title">Ask Targeted Questions</div>
    <div class="home-card-copy">
        Use measure-specific or cross-measure Q&amp;A to surface relevant answers quickly, with
        local dataset matching and broader fallback behavior when needed.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
        st.markdown("---")
        st.markdown(
            """
<div class="home-card" style="margin-top: 0.1rem;">
    <div class="home-card-title">Upload PDF and Refresh Dataset</div>
    <div class="home-card-copy">
        Upload a HEDIS PDF, convert it into structured JSON, and replace the active dataset used by the app.
    </div>
</div>
""",
            unsafe_allow_html=True,
        )
        if not st.session_state["is_user_authenticated"]:
            st.warning("Sign in with the shared user credentials to move beyond Home.")
        else:
            if admin_auth_ready:
                admin_col, admin_status_col = st.columns([1, 1], gap="medium")
                with admin_col:
                    if not st.session_state["is_admin_authenticated"]:
                        st.text_input("Admin Password", type="password", key="admin_password")
                        st.button(
                            "Unlock Uploads",
                            use_container_width=True,
                            on_click=login_admin,
                            args=(base_dir,),
                        )
                    else:
                        st.button("Lock Uploads", use_container_width=True, on_click=logout_admin)
                with admin_status_col:
                    if not st.session_state["is_admin_authenticated"]:
                        st.caption("Enter the admin password to enable PDF upload and dataset replacement.")
                        if st.session_state.get("admin_login_error"):
                            st.error(st.session_state["admin_login_error"])
                    else:
                        st.success("Admin upload access enabled.")
            else:
                st.info("Admin upload password is not configured yet.")

            if not st.session_state["is_admin_authenticated"]:
                st.warning("Admin unlock is required before anyone can upload or replace the dataset.")
                return

            upload_col, info_col = st.columns([1, 1], gap="medium")
            with upload_col:
                uploaded_pdf = st.file_uploader(
                    "",
                    accept_multiple_files=False,
                    help="This converts the PDF into the JSON structure used by the rest of the POC.",
                    label_visibility="collapsed",
                )
                valid_pdf_upload = is_pdf_upload(uploaded_pdf)
                if uploaded_pdf is not None and not valid_pdf_upload:
                    st.warning("Please choose a PDF file before converting.")
                button_left_col, button_right_spacer = st.columns([0.62, 0.38], gap="small")
                with button_left_col:
                    if st.button(
                        "Convert PDF to JSON",
                        disabled=(not api_key) or (uploaded_pdf is None) or (not valid_pdf_upload),
                        use_container_width=True,
                    ):
                        try:
                            handle_pdf_upload(base_dir, uploaded_pdf)
                        except Exception as exc:
                            st.error(f"PDF conversion failed: {exc}")
            with info_col:
                if uploaded_pdf is not None:
                    st.markdown(
                        f"""
<div class="uploaded-file-bar">
    Selected file: <span class="uploaded-file-name">{uploaded_pdf.name}</span>
</div>
""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        """
<div class="uploaded-file-bar">
    No PDF selected yet.
</div>
""",
                        unsafe_allow_html=True,
                    )
            if st.session_state.get("pdf_upload_success"):
                st.success(st.session_state["pdf_upload_success"])
            if uploaded_pdf is not None and valid_pdf_upload:
                st.caption("Ready to process the selected PDF.")
            elif uploaded_pdf is not None:
                st.caption("Choose a file with a .pdf extension to enable conversion.")
            else:
                st.caption("Choose a PDF file to enable conversion.")

    elif current_view == measure_details_menu_label:
        if selected_measure is None:
            st.info("Choose a measure from the left control panel to view details.")
        else:
            render_measure_details(selected_measure)

    elif current_view == "Overview":
        st.write("Generate a high-level executive summary of the HEDIS dataset.")
        if st.button("Generate Overview", use_container_width=False, disabled=not api_key):
            with st.spinner("Generating overview..."):
                try:
                    st.session_state["overview_result"] = generate_overview(measures)
                    st.session_state.pop("overview_error", None)
                except Exception as exc:
                    st.session_state["overview_error"] = str(exc)
                    st.session_state.pop("overview_result", None)
        if "overview_error" in st.session_state:
            st.error(f"Overview generation failed: {st.session_state['overview_error']}")
        elif "overview_result" in st.session_state:
            st.markdown('<div class="overview-panel">', unsafe_allow_html=True)
            st.markdown(format_answer_as_bullets(st.session_state["overview_result"]))
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.caption("No overview generated yet.")

    elif current_view == ask_measure_menu_label:
        if selected_measure is None:
            st.info("Choose a measure from the left control panel to ask about it.")
            return

        left_col, right_col = st.columns([1.45, 0.9], gap="large")
        selected_measure_abbreviation = get_measure_abbreviation(selected_measure_name)
        current_custom_measure_question = st.session_state.get(
            "custom_measure_question", ""
        ).strip()
        current_selected_measure_question = st.session_state.get(
            "measure_question_select",
            measure_questions[0] if measure_questions else "",
        )
        current_measure_question_preview = (
            current_custom_measure_question or current_selected_measure_question
        )

        with left_col:
            st.markdown(f"**Selected measure:** {selected_measure_name}")
            st.markdown(
                f"""
<div class="question-preview">
<strong>Question to be answered:</strong><br>{current_measure_question_preview}
</div>
""",
                unsafe_allow_html=True,
            )
            if "measure_result" in st.session_state:
                result = st.session_state["measure_result"]
                if result["mode"] == "measure_to_local_dataset":
                    st.info(
                        "The selected measure was not enough, so the app expanded to the local dataset."
                    )
                elif result["mode"] != "selected_measure":
                    st.warning(
                        "The selected measure was not enough, so the app used an external fallback answer."
                    )

                st.markdown('<div class="answer-panel">', unsafe_allow_html=True)
                st.markdown(format_answer_as_bullets(result["answer"]))
                st.markdown("</div>", unsafe_allow_html=True)
                render_search_results(result["results"])

        with right_col:
            ask_measure_clicked = st.button(
                "Ask AI",
                use_container_width=True,
                disabled=not api_key,
            )
            st.write("Custom question")
            custom_measure_question = st.text_input(
                "Or enter your own measure question",
                placeholder="Example: Does this measure define any exclusions for hospice patients?",
                key="custom_measure_question",
                label_visibility="collapsed",
            )
            st.write("Suggested questions")
            selected_measure_prompt = st.selectbox(
                "Choose a suggested question",
                measure_questions,
                key="measure_question_select",
                label_visibility="collapsed",
                on_change=use_selected_measure_question,
            )

        measure_question = custom_measure_question.strip() or selected_measure_prompt
        if ask_measure_clicked:
            with st.spinner("Generating answer..."):
                try:
                    st.session_state["measure_result"] = answer_measure_with_fallback(
                        selected_measure, measures, measure_question
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Answer generation failed: {exc}")
                    st.session_state.pop("measure_result", None)

    elif current_view == "Ask About All Measures":
        left_col, right_col = st.columns([1.45, 0.9], gap="large")
        current_custom_dataset_question = st.session_state.get(
            "custom_dataset_question", ""
        ).strip()
        current_selected_dataset_question = st.session_state.get(
            "dataset_question_select",
            dataset_questions[0] if dataset_questions else "",
        )
        current_dataset_question_preview = (
            current_custom_dataset_question or current_selected_dataset_question
        )

        with left_col:
            st.markdown(
                f"""
<div class="question-preview">
<strong>Question to be answered:</strong><br>{current_dataset_question_preview}
</div>
""",
                unsafe_allow_html=True,
            )
            if "dataset_result" in st.session_state:
                result = st.session_state["dataset_result"]
                if result["mode"] != "local_dataset":
                    st.warning(
                        "The local dataset did not clearly answer this, so a general external AI answer was used."
                    )

                st.markdown('<div class="answer-panel">', unsafe_allow_html=True)
                st.markdown(format_answer_as_bullets(result["answer"]))
                st.markdown("</div>", unsafe_allow_html=True)
                render_search_results(result["results"])

        with right_col:
            ask_dataset_clicked = st.button(
                "Ask AI About All Measures",
                use_container_width=True,
                disabled=not api_key,
            )
            st.write("Custom question")
            custom_dataset_question = st.text_input(
                "Or enter your own dataset question",
                placeholder="Example: Which measures involve preventive care for children or adolescents?",
                key="custom_dataset_question",
                label_visibility="collapsed",
                on_change=handle_dataset_question_change,
            )
            st.write("Suggested questions")
            selected_dataset_prompt = st.selectbox(
                "Choose a dataset question",
                dataset_questions,
                key="dataset_question_select",
                label_visibility="collapsed",
                on_change=use_selected_dataset_question,
            )

        dataset_question = custom_dataset_question.strip() or selected_dataset_prompt
        if ask_dataset_clicked:
            with st.spinner("Generating answer..."):
                try:
                    st.session_state["dataset_result"] = hybrid_answer(
                        measures, dataset_question
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Dataset answer generation failed: {exc}")
                    st.session_state.pop("dataset_result", None)


if __name__ == "__main__":
    main()
