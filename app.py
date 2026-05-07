import os
import uuid
import subprocess
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.utils import secure_filename
import fitz
from PIL import Image
import io
import easyocr
import base64
import requests
import shutil
import re
import json
import mysql.connector
from mysql.connector import Error
from html import unescape

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['DB_CONFIG'] = {
    'host': 'localhost',
    'user': 'job',
    'password': 'Xdman123456@',
    'database': 'job'
}

IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'tif', 'tiff', 'webp'}
ALLOWED_EXTENSIONS = {'pdf', *IMAGE_EXTENSIONS}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

def init_db():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor()

        # Create tables based on normalized schema (MySQL syntax)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS exams (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL
        )''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS years (
            id INT AUTO_INCREMENT PRIMARY KEY,
            year VARCHAR(100) UNIQUE NOT NULL
        )''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            text TEXT NOT NULL
        )''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS options (
            id INT AUTO_INCREMENT PRIMARY KEY,
            text TEXT,
            type VARCHAR(50) DEFAULT 'text',
            image_base64 LONGTEXT
        )''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS mcqs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            question_id INT,
            option_ids TEXT, -- JSON string of option IDs
            answer_index INT, -- 1-4
            answer_id INT, -- FK to options.id
            explanation TEXT,
            language VARCHAR(50),
            subject VARCHAR(255),
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (answer_id) REFERENCES options(id)
        )''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS exam_questions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            exam_id INT,
            year_id INT,
            mcq_id INT,
            FOREIGN KEY (exam_id) REFERENCES exams(id),
            FOREIGN KEY (year_id) REFERENCES years(id),
            FOREIGN KEY (mcq_id) REFERENCES mcqs(id)
        )''')

        conn.commit()
        cursor.close()
        conn.close()
        print("MySQL Database initialized successfully.")
    except Error as e:
        print(f"Error while connecting to MySQL: {e}")

# Initialize DB on startup
init_db()

OPENAI_COMPAT_BASE_URL = os.environ.get("OPENAI_COMPAT_BASE_URL", "http://localhost:8045/v1")
OPENAI_COMPAT_API_KEY = os.environ.get("OPENAI_COMPAT_API_KEY", "sk-28d07728e1aa4ac5adb0d1fc09b7d743")
OPENAI_COMPAT_MODEL = os.environ.get("OPENAI_COMPAT_MODEL", "gemini-3-flash")
OPENAI_COMPAT_MODEL_FALLBACKS = os.environ.get("OPENAI_COMPAT_MODEL_FALLBACKS", "")
OPENAI_COMPAT_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_COMPAT_TIMEOUT_SECONDS", "240"))
OPENAI_COMPAT_PROBE_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_COMPAT_PROBE_TIMEOUT_SECONDS", "12"))
OPENAI_COMPAT_ENABLE_POSTPROCESS = os.environ.get("OPENAI_COMPAT_ENABLE_POSTPROCESS", "false").lower() == "true"
AISTUDIO_TIMEOUT_SECONDS = float(os.environ.get("AISTUDIO_TIMEOUT_SECONDS", "300"))
AISTUDIO_MAX_GEMINI_IMAGES = int(os.environ.get("AISTUDIO_MAX_GEMINI_IMAGES", "80"))

MCQ_DATASET_SYSTEM_PROMPT = """
You are an AI data processing agent designed to convert OCR-extracted text from Bangla, English, or mixed-language government job preparation books into structured, high-quality datasets.

Objective:
Transform noisy OCR text into:
1. Structured MCQ data for database storage
2. Clean contextual text for AI retrieval / RAG systems

Text cleaning rules:
- Normalize whitespace.
- Fix broken words only when the correction is clear from context.
- Remove irrelevant symbols, headers, page numbers, and noise.
- Preserve Bangla characters correctly (Unicode range U+0980-U+09FF).
- Keep semantic meaning intact.

MCQ extraction rules:
- Extract all complete MCQs from the cleaned text.
- Each MCQ must contain question, exactly 4 options, answer, explanation, language, subject, exam, and year.
- Options may be marked A/B/C/D or ক/খ/গ/ঘ.
- Text-only options may be returned as strings.
- When an option is represented by an image, return an object with type "image", text null, and imageBase64 set to the provided data URL.
- When an option has both visible text and an associated image, return an object with type "image", text set to the visible label/text, and imageBase64 set to the provided data URL.
- answer must be the correct option number as an integer: 1, 2, 3, or 4.
- If an answer/explanation is explicitly present but does not match any of the 4 options, use 0.
- If no answer evidence is explicitly detectable, use null.
- If the explanation is not available, use null.
- If options are unclear, skip that MCQ.
- For AI Studio page-image inputs, do not omit visible numbered questions. Use the page image to recover answers/options that are missing from markdown text.
- Do not hallucinate answers, explanations, subjects, exams, years, or missing options.
- Prefer high accuracy over completeness.
- Do not merge multiple questions incorrectly.
- language must be exactly "bn", "en", or "mixed".

Context generation rules:
- Generate one clean declarative knowledge paragraph for each MCQ when enough facts are known.
- Convert MCQs into factual statements.
- Keep both Bangla and English when present.
- If the correct answer is null and a factual context cannot be determined safely, omit that context.

Return ONLY valid JSON. Do not include markdown, comments, or explanation.
The JSON object must have this exact top-level structure:
{
  "mcqs": [
    {
      "question": "",
      "options": [
        {"type": "text", "text": "", "imageBase64": null},
        {"type": "text", "text": "", "imageBase64": null},
        {"type": "text", "text": "", "imageBase64": null},
        {"type": "image", "text": null, "imageBase64": "data:image/jpeg;base64,..."}
      ],
      "answer": 1,
      "explanation": null,
      "language": "bn",
      "subject": null,
      "exam": null,
      "year": null
    }
  ],
  "contexts": []
}
""".strip()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_extension(path):
    return os.path.splitext(path)[1].lower().lstrip('.')


def is_pdf_file(path):
    return get_file_extension(path) == 'pdf'


def is_image_file(path):
    return get_file_extension(path) in IMAGE_EXTENSIONS


def get_easyocr_langs(lang):
    mapping = {
        'english': ['en'],
        'bengali': ['bn'],
        'both': ['en', 'bn']
    }
    return mapping.get(lang, ['en'])


# Cache for EasyOCR readers to avoid re-initializing
OcrReaders = {}
OpenAICompatClient = None
OpenAICompatCapabilities = None
OpenAICompatProbeErrors = []
OpenAICompatModelListFailed = False

def get_reader(langs):
    lang_key = tuple(sorted(langs))
    if lang_key not in OcrReaders:
        # Initialize reader (this downloads models if first time)
        OcrReaders[lang_key] = easyocr.Reader(list(langs))
    return OcrReaders[lang_key]


def get_openai_compat_client():
    global OpenAICompatClient

    if OpenAICompatClient is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The OpenAI Python package is required for the OpenAI-compatible engine. "
                "Install it with: pip install openai"
            ) from exc

        OpenAICompatClient = OpenAI(
            base_url=OPENAI_COMPAT_BASE_URL,
            api_key=OPENAI_COMPAT_API_KEY,
            timeout=OPENAI_COMPAT_TIMEOUT_SECONDS,
            max_retries=0,
        )

    return OpenAICompatClient


def call_openai_compatible_chat(model, messages, temperature=0, timeout=None):
    client = get_openai_compat_client()
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=timeout or OPENAI_COMPAT_TIMEOUT_SECONDS,
    )


def list_openai_compatible_models():
    global OpenAICompatModelListFailed

    client = get_openai_compat_client()

    try:
        response = client.models.list()
        OpenAICompatModelListFailed = False
        return [model.id for model in response.data if getattr(model, "id", None)]
    except Exception as exc:
        OpenAICompatModelListFailed = True
        OpenAICompatProbeErrors.append(f"Could not list models from {OPENAI_COMPAT_BASE_URL}: {exc}")
        return []


def get_openai_compatible_model_candidates():
    discovered = list_openai_compatible_models()
    preferred = []

    if OPENAI_COMPAT_MODEL:
        preferred.append(OPENAI_COMPAT_MODEL)

    preferred.extend(
        model.strip()
        for model in OPENAI_COMPAT_MODEL_FALLBACKS.split(",")
        if model.strip()
    )

    if discovered or OPENAI_COMPAT_MODEL_FALLBACKS:
        preferred.extend(
            [
                "gemini-3-flash",
                "gemini-3-pro",
                "gemini-3-pro-high",
                "gemini-3-pro-low",
                "gemini-3-pro-preview",
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-2.0-flash",
                "gpt-4o-mini",
                "gpt-4o",
            ]
        )

    candidates = []
    seen = set()
    for model in preferred + discovered:
        if not model or model in seen:
            continue
        seen.add(model)
        candidates.append(model)

    return candidates


def get_openai_compatible_message_text(response):
    message = response.choices[0].message.content
    if isinstance(message, str):
        return message.strip()

    if isinstance(message, list):
        parts = []
        for item in message:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
        return "\n".join(part.strip() for part in parts if part and part.strip())

    return ""


def supports_openai_text_model(model):
    try:
        response = call_openai_compatible_chat(
            model,
            [{"role": "user", "content": "Reply with exactly OK"}],
            temperature=0,
            timeout=OPENAI_COMPAT_PROBE_TIMEOUT_SECONDS,
        )
        content = get_openai_compatible_message_text(response)
        if content:
            return True
        OpenAICompatProbeErrors.append(f"Model {model} returned an empty text response.")
        return False
    except Exception as exc:
        OpenAICompatProbeErrors.append(f"Model {model} text probe failed: {exc}")
        return False


def supports_openai_vision_model(model):
    tiny_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1cAAAAASUVORK5CYII="

    try:
        response = call_openai_compatible_chat(
            model,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image? Reply with one short sentence."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{tiny_image}"},
                        },
                    ],
                }
            ],
            temperature=0,
            timeout=OPENAI_COMPAT_PROBE_TIMEOUT_SECONDS,
        )
        content = get_openai_compatible_message_text(response)
        return bool(content)
    except Exception:
        return False


def get_openai_compat_capabilities():
    global OpenAICompatCapabilities

    if OpenAICompatCapabilities is not None:
        return OpenAICompatCapabilities

    OpenAICompatProbeErrors.clear()
    candidates = get_openai_compatible_model_candidates()
    text_model = None

    for model in candidates:
        if supports_openai_text_model(model):
            text_model = model
            break

    if not text_model:
        details = " ".join(OpenAICompatProbeErrors[-4:]).strip()
        configured = OPENAI_COMPAT_MODEL or "(not set)"
        suffix = f" Details: {details}" if details else ""
        raise RuntimeError(
            "No working text model was found on the OpenAI-compatible endpoint. "
            f"Base URL: {OPENAI_COMPAT_BASE_URL}. Configured model: {configured}. "
            "Start the proxy service or set OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_MODEL "
            "to a model that supports chat completions."
            f"{suffix}"
        )

    vision_model = text_model if supports_openai_vision_model(text_model) else None

    OpenAICompatCapabilities = {
        "text_model": text_model,
        "vision_model": vision_model,
        "vision_supported": vision_model is not None,
    }
    return OpenAICompatCapabilities


def prepare_openai_compatible_image_data_url(image):
    # Keep request bodies small enough for local OpenAI-compatible servers.
    max_payload_bytes = 3_500_000
    attempts = [
        (1800, 85),
        (1400, 75),
        (1100, 65),
        (900, 55),
    ]

    source = image.convert("RGB")

    for max_edge, quality in attempts:
        candidate = source.copy()
        candidate.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
        image_bytes = buffer.getvalue()

        if len(image_bytes) <= max_payload_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"

    # Fallback to the smallest attempt even if the server limit is unusually low.
    buffer = io.BytesIO()
    source.thumbnail((700, 700), Image.Resampling.LANCZOS)
    source.save(buffer, format="JPEG", quality=45, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def extract_text_with_openai_compatible(image, page_number, model):
    data_url = prepare_openai_compatible_image_data_url(image)

    response = call_openai_compatible_chat(
        model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are an OCR engine. Extract all visible text from this document page. "
                            "Preserve line breaks and reading order as well as possible. "
                            "Return only the extracted text."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    },
                ],
            }
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    if message:
        return message

    raise RuntimeError(f"No text returned by OpenAI-compatible engine for page {page_number}.")


def get_document_page_images(file_path, dpi=200):
    page_images = []

    if is_pdf_file(file_path):
        doc = fitz.open(file_path)
        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                pix = page.get_pixmap(dpi=dpi)
                img_bytes = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_bytes))
                page_images.append((page_num + 1, img))
        finally:
            doc.close()
    elif is_image_file(file_path):
        img = Image.open(file_path)
        page_images.append((1, img))
    else:
        raise ValueError("Unsupported file type. Please upload a PDF or image file.")

    return page_images


def extract_clean_text_with_gemini(image, page_number, model):
    data_url = prepare_openai_compatible_image_data_url(image)

    response = call_openai_compatible_chat(
        model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "OCR this document page and clean the result. Preserve Bangla and English exactly. "
                            "Keep question numbers, options, formulas, answer markers such as 'উ. ক', and explanations. "
                            "Do not summarize. Return only cleaned page text."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    if message:
        return message

    raise RuntimeError(f"No text returned by Gemini engine for page {page_number}.")


def transform_page_image_to_mcq_dataset_with_gemini(image, page_number, model, page_text=None):
    data_url = prepare_openai_compatible_image_data_url(image)

    response = call_openai_compatible_chat(
        model,
        messages=[
            {"role": "system", "content": MCQ_DATASET_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"OCR, clean, and structure all complete MCQs visible on page {page_number}. "
                            "Use both the cleaned OCR text below and the page image directly for OCR, options, formulas, "
                            "answer markers, and explanations. "
                            "Do not omit visible numbered questions. Right-side printed markers like 'উ. ক', 'উ. খ', "
                            "'উ. গ', and 'উ. ঘ' map to answer 1, 2, 3, and 4. "
                            "If an explanation gives an answer that is not one of the four options, set answer to 0. "
                            "If no answer evidence is visible, set answer to null. "
                            "Include image/pattern/diagram questions too; if option images cannot be cropped, describe each option image briefly in text. "
                            "Return only strict JSON.\n\n"
                            "Cleaned OCR text from this same page:\n"
                            f"{page_text or ''}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    try:
        parsed = json.loads(strip_json_code_fence(message))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini structured extraction returned invalid JSON on page {page_number}.") from exc

    return normalize_mcq_dataset(parsed)


def normalize_digit_text(value):
    digit_map = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
    return str(value).translate(digit_map)


def get_longest_consecutive_question_count(text):
    numbers = []
    for match in re.finditer(r"(?<![\d০-৯])([০-৯0-9]{1,3})\s*[\.)।]", text or ""):
        try:
            number = int(normalize_digit_text(match.group(1)))
        except ValueError:
            continue
        if 1 <= number <= 250:
            numbers.append(number)

    if not numbers:
        return 0

    unique_numbers = sorted(set(numbers))
    best = 1
    current = 1
    for previous, number in zip(unique_numbers, unique_numbers[1:]):
        if number == previous + 1:
            current += 1
            best = max(best, current)
        else:
            current = 1

    return best


def get_question_number_from_text(text):
    match = re.match(r"^\s*([০-৯0-9]{1,3})\s*[\.)।]", text or "")
    if not match:
        return None
    try:
        return int(normalize_digit_text(match.group(1)))
    except ValueError:
        return None


def extract_missing_mcqs_with_gemini(image, page_number, page_text, existing_dataset, model):
    data_url = prepare_openai_compatible_image_data_url(image)
    existing_questions = [
        mcq.get("question") or ""
        for mcq in existing_dataset.get("mcqs", [])
    ]

    response = call_openai_compatible_chat(
        model,
        messages=[
            {"role": "system", "content": MCQ_DATASET_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"On page {page_number}, some visible numbered MCQs were missed. "
                            "Extract ONLY complete MCQs visible on the page image that are NOT already in the previous list. "
                            "Pay special attention to image, diagram, series, projectile, pattern, and figure-based questions. "
                            "Use the cleaned OCR text and the page image. If an option is a diagram and cannot be cropped, "
                            "describe it briefly as text. Return only strict JSON.\n\n"
                            "Previously extracted questions:\n"
                            f"{json.dumps(existing_questions, ensure_ascii=False)}\n\n"
                            "Cleaned OCR text:\n"
                            f"{page_text}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    try:
        parsed = json.loads(strip_json_code_fence(message))
    except json.JSONDecodeError:
        return {"mcqs": [], "contexts": []}

    return normalize_mcq_dataset(parsed)


def should_fallback_openai_vision(exc):
    message = str(exc).lower()
    markers = [
        "unable to process input image",
        "unknown error",
        "server_error",
        "error code: 500",
    ]
    return any(marker in message for marker in markers)


def extract_text_with_tesseract_image(image, language):
    import pytesseract
    from pytesseract import Output

    if not configure_tesseract_command(pytesseract):
        raise RuntimeError(
            "Tesseract executable not found. Install Tesseract OCR or add tesseract.exe to your PATH."
        )

    lang_map = {'english': 'eng', 'bengali': 'ben', 'both': 'eng+ben'}
    tess_lang = lang_map.get(language, 'eng')

    temp_img_path = os.path.join(app.config['OUTPUT_FOLDER'], f'temp_{uuid.uuid4().hex}.png')
    image.save(temp_img_path)

    try:
        data = pytesseract.image_to_data(temp_img_path, lang=tess_lang, output_type=Output.DICT)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)

    ocr_results = []
    n_boxes = len(data['text'])
    for i in range(n_boxes):
        if int(data['conf'][i]) > -1:
            val = data['text'][i].strip()
            if val:
                x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                bounds = [[x, y], [x+w, y], [x+w, y+h], [x, y+h]]
                ocr_results.append((bounds, val, data['conf'][i]))

    return reconstruct_layout(ocr_results)


def extract_text_with_easyocr_image(image, language):
    langs = get_easyocr_langs(language)
    reader = get_reader(langs)

    temp_img_path = os.path.join(app.config['OUTPUT_FOLDER'], f'temp_{uuid.uuid4().hex}.png')
    image.save(temp_img_path)

    try:
        result = reader.readtext(temp_img_path, detail=1)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)

    return reconstruct_layout(result)


def extract_text_with_local_ocr_fallback(image, language):
    try:
        return extract_text_with_tesseract_image(image, language)
    except Exception:
        return extract_text_with_easyocr_image(image, language)


def cleanup_ocr_text_with_openai_compatible(raw_text, model):
    if not raw_text or not raw_text.strip():
        return ""

    response = call_openai_compatible_chat(
        model,
        messages=[
            {
                "role": "user",
                "content": (
                    "You are a conservative OCR post-processor. "
                    "Do not paraphrase. Do not summarize. Do not add any words that are not present. "
                    "Keep line breaks and order. "
                    "If uncertain, keep the original token unchanged. "
                    "Return only cleaned OCR text.\n\n"
                    f"{raw_text}"
                ),
            }
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    if message:
        return message

    return raw_text


def strip_json_code_fence(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_option_number_from_text(text, options):
    if not text:
        return None

    text_value = str(text)
    markers = [
        r"(?:correct\s+answer|answer|ans)\s*[:：\-]?\s*([^\n,;।]+)",
        r"(?:সঠিক\s+উত্তর|উত্তর)\s*[:：\-]?\s*([^\n,;।]+)",
    ]

    for pattern in markers:
        match = re.search(pattern, text_value, flags=re.IGNORECASE)
        if match:
            normalized = normalize_answer_to_option_number(match.group(1), options)
            if normalized is not None:
                return normalized

    return normalize_answer_to_option_number(text_value, options)


def contains_answer_evidence(text):
    if not text:
        return False

    text_value = str(text).strip()
    if not text_value:
        return False

    markers = [
        r"(?:correct\s+answer|answer|ans)\s*[:：\-]?\s*[^\n,;।]+",
        r"(?:সঠিক\s+উত্তর|উত্তর)\s*[:：\-]?\s*[^\n,;।]+",
        r"(?:উ)\s*[\.:：\-]\s*[কখগঘabcdABCD]",
    ]

    return any(re.search(pattern, text_value, flags=re.IGNORECASE) for pattern in markers)


def normalize_answer_to_option_number(answer, options):
    if answer is None:
        return None

    if isinstance(answer, int) and 0 <= answer <= 4:
        return answer

    answer_text = str(answer).strip()
    if not answer_text:
        return None

    if answer_text.isdigit():
        answer_number = int(answer_text)
        if 0 <= answer_number <= 4:
            return answer_number

    label_map = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "ক": 1,
        "খ": 2,
        "গ": 3,
        "ঘ": 4,
    }
    cleaned_label = answer_text.strip(" .):।-").lower()
    cleaned_label = re.sub(r"^(?:উ|উত্তর|ans|answer)\s*[\.:：\-]?\s*", "", cleaned_label, flags=re.IGNORECASE)
    if cleaned_label in label_map:
        return label_map[cleaned_label]

    normalized_answer = re.sub(r"\s+", " ", answer_text).strip().lower()
    for index, option in enumerate(options, start=1):
        option_text = ""
        if isinstance(option, dict):
            option_text = option.get("text") or ""
        else:
            option_text = str(option or "")

        normalized_option = re.sub(r"\s+", " ", str(option_text)).strip().lower()
        if normalized_option and normalized_option == normalized_answer:
            return index

    return None


def normalize_mcq_dataset(value, image_lookup=None):
    if not isinstance(value, dict):
        raise ValueError("Structured extraction did not return a JSON object.")

    raw_mcqs = value.get("mcqs", [])
    raw_contexts = value.get("contexts", [])
    if not isinstance(raw_mcqs, list):
        raw_mcqs = []
    if not isinstance(raw_contexts, list):
        raw_contexts = []

    normalized_mcqs = []
    for item in raw_mcqs:
        if not isinstance(item, dict):
            continue

        question = str(item.get("question") or "").strip()
        options = item.get("options")
        if not question or not isinstance(options, list) or len(options) != 4:
            continue

        normalized_options = []
        for option in options:
            if isinstance(option, dict):
                option_type = str(option.get("type") or "text").strip().lower()
                text = option.get("text")
                text = str(text).strip() if text is not None and str(text).strip() else None
                image_base64 = option.get("imageBase64") or option.get("image_base64") or option.get("image")
                image_base64 = str(image_base64).strip() if image_base64 else None
                image_ref = option.get("imageRef") or option.get("image_ref")
                image_ref = str(image_ref).strip() if image_ref else None

                if image_lookup:
                    if image_base64 and not image_base64.startswith("data:image/"):
                        image_base64 = image_lookup.get(image_base64) or image_base64
                    if not image_base64 and image_ref:
                        image_base64 = image_lookup.get(image_ref)

                if option_type not in {"text", "image"}:
                    option_type = "image" if image_base64 else "text"

                if option_type == "image" and not image_base64:
                    continue
                if option_type == "text" and not text:
                    continue

                normalized_options.append({
                    "type": option_type,
                    "text": text,
                    "imageBase64": image_base64,
                })
            else:
                text = str(option or "").strip()
                if text:
                    normalized_options.append({
                        "type": "text",
                        "text": text,
                        "imageBase64": None,
                    })

        if len(normalized_options) != 4:
            continue

        explanation = item.get("explanation")
        if isinstance(explanation, str):
            explanation = explanation.strip() or None
        elif explanation is not None:
            explanation = str(explanation).strip() or None

        raw_answer = item.get("answer")
        answer = normalize_answer_to_option_number(raw_answer, normalized_options)
        if answer is None and explanation:
            answer = extract_option_number_from_text(explanation, normalized_options)
            if answer is None and contains_answer_evidence(explanation):
                answer = 0
        elif answer is None and contains_answer_evidence(raw_answer):
            answer = 0

        language = item.get("language")
        if language not in {"bn", "en", "mixed"}:
            option_text = " ".join(option.get("text") or "" for option in normalized_options)
            language = detect_text_language(" ".join([question, option_text]))

        subject = item.get("subject")
        subject = str(subject).strip() if subject is not None and str(subject).strip() else None

        exam = item.get("exam")
        exam = str(exam).strip() if exam is not None and str(exam).strip() else None

        year = item.get("year")
        year = str(year).strip() if year is not None and str(year).strip() else None

        normalized_mcqs.append({
            "question": question,
            "options": normalized_options,
            "answer": answer,
            "explanation": explanation,
            "language": language,
            "subject": subject,
            "exam": exam,
            "year": year,
        })

    normalized_contexts = [
        str(context).strip()
        for context in raw_contexts
        if isinstance(context, str) and context.strip()
    ]

    return {
        "mcqs": normalized_mcqs,
        "contexts": normalized_contexts,
    }


def detect_text_language(text):
    has_bangla = bool(re.search(r"[\u0980-\u09FF]", text or ""))
    has_latin = bool(re.search(r"[A-Za-z]", text or ""))
    if has_bangla and has_latin:
        return "mixed"
    if has_bangla:
        return "bn"
    return "en"


def transform_ocr_text_to_mcq_dataset(raw_text, model):
    if not raw_text or not raw_text.strip():
        return {"mcqs": [], "contexts": []}

    response = call_openai_compatible_chat(
        model,
        messages=[
            {"role": "system", "content": MCQ_DATASET_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Convert the following OCR text into the required strict JSON dataset.\n\n"
                    f"{raw_text}"
                ),
            },
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)

    try:
        parsed = json.loads(strip_json_code_fence(message))
    except json.JSONDecodeError as exc:
        raise ValueError("Structured MCQ extraction returned invalid JSON.") from exc

    return normalize_mcq_dataset(parsed)


def get_image_mime_type(path_or_url):
    ext = os.path.splitext((path_or_url or "").split("?", 1)[0])[1].lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext in {".tif", ".tiff"}:
        return "image/tiff"
    if ext == ".bmp":
        return "image/bmp"
    return "image/jpeg"


def image_bytes_to_data_url(image_bytes, path_or_url):
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{get_image_mime_type(path_or_url)};base64,{encoded}"


def download_image_bytes(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def replace_markdown_image_sources_with_refs(markdown_text, page_number, page_images):
    text = markdown_text or ""
    for img_path in page_images.keys():
        image_ref = f"page_{page_number}:{img_path}"
        text = text.replace(f'src="{img_path}"', f'src="{image_ref}"')
        text = text.replace(f"src='{img_path}'", f"src='{image_ref}'")
    return text


def build_source_page_image_assets(file_path):
    assets = []

    if is_pdf_file(file_path):
        doc = fitz.open(file_path)
        try:
            for page_index, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=180)
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                assets.append({
                    "id": f"page_{page_index}:full_page",
                    "path": f"page_{page_index}.jpg",
                    "data_url": prepare_openai_compatible_image_data_url(image),
                    "kind": "full_page",
                })
        finally:
            doc.close()
    elif is_image_file(file_path):
        image = Image.open(file_path)
        assets.append({
            "id": "page_1:full_page",
            "path": os.path.basename(file_path),
            "data_url": prepare_openai_compatible_image_data_url(image),
            "kind": "full_page",
        })

    return assets


def transform_aistudio_layout_to_mcq_dataset(markdown_text, image_assets, model):
    if not markdown_text or not markdown_text.strip():
        return {"mcqs": [], "contexts": []}

    image_lookup = {
        asset["id"]: asset["data_url"]
        for asset in image_assets
        if asset.get("id") and asset.get("data_url")
    }

    content = [
        {
            "type": "text",
            "text": (
                "Convert this AI Studio/PaddleOCR layout output into the required strict JSON dataset.\n\n"
                "The markdown contains image placeholders like src=\"page_1:imgs/example.jpg\". "
                "Those image IDs are attached after the markdown as image inputs. "
                "Full-page images are also attached with IDs like page_1:full_page. "
                "Use the full-page images to read right-margin printed answer markers such as "
                "\"উ. ক\", \"উ. খ\", \"উ. গ\", or \"উ. ঘ\"; map them to answer 1, 2, 3, or 4. "
                "Use both OCR text and attached images. If an MCQ option is visual, set that option to "
                "{\"type\":\"image\",\"text\":<label or null>,\"imageRef\":\"<matching image id>\"}. "
                "If an option has text only, use {\"type\":\"text\",\"text\":\"...\",\"imageBase64\":null}. "
                "Do not leave answer null when a printed answer marker is visible in the full-page image. "
                "Return only JSON; do not include markdown or explanation.\n\n"
                f"{markdown_text}"
            ),
        }
    ]

    ordered_assets = sorted(
        image_assets,
        key=lambda asset: 0 if asset.get("kind") == "full_page" else 1
    )

    for asset in ordered_assets[:AISTUDIO_MAX_GEMINI_IMAGES]:
        data_url = asset.get("data_url")
        image_id = asset.get("id")
        if not data_url or not image_id:
            continue
        content.append({"type": "text", "text": f"Image ID: {image_id}"})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    response = call_openai_compatible_chat(
        model,
        messages=[
            {"role": "system", "content": MCQ_DATASET_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)

    try:
        parsed = json.loads(strip_json_code_fence(message))
    except json.JSONDecodeError as exc:
        raise ValueError("Structured MCQ extraction returned invalid JSON.") from exc

    return normalize_mcq_dataset(parsed, image_lookup=image_lookup)


def repair_aistudio_mcq_with_page_image(mcq, page_text, page_assets, model):
    if not mcq or not page_assets:
        return mcq

    image_lookup = {
        asset["id"]: asset["data_url"]
        for asset in page_assets
        if asset.get("id") and asset.get("data_url")
    }

    content = [
        {
            "type": "text",
            "text": (
                "Repair this single MCQ extraction by re-reading the attached page image and layout text. "
                "The previous extraction has a missing or contradictory answer. "
                "If the page shows a right-margin marker like \"উ. ক\", \"উ. খ\", \"উ. গ\", or \"উ. ঘ\", "
                "map it to answer 1, 2, 3, or 4. This right-margin marker is authoritative. "
                "Ignore the previous options if they conflict with the page image. If an explanation says "
                "\"সঠিক উত্তর: <value>\", make sure the option containing that value is captured from the page image "
                "and use its option number. If the explanation answer is visible but not present in any option, "
                "set answer to 0. Correct OCR mistakes in the option text from the page image. "
                "Return ONLY valid JSON with this exact shape: "
                "{\"mcqs\":[{\"question\":\"\",\"options\":[{\"type\":\"text\",\"text\":\"\",\"imageBase64\":null}],"
                "\"answer\":1,\"explanation\":null,\"language\":\"bn\",\"subject\":null,\"exam\":null}],\"contexts\":[]}.\n\n"
                "Previous MCQ JSON:\n"
                f"{json.dumps(mcq, ensure_ascii=False)}\n\n"
                "Page layout text:\n"
                f"{page_text}"
            ),
        }
    ]

    ordered_assets = sorted(
        page_assets,
        key=lambda asset: 0 if asset.get("kind") == "full_page" else 1
    )
    for asset in ordered_assets[:AISTUDIO_MAX_GEMINI_IMAGES]:
        data_url = asset.get("data_url")
        image_id = asset.get("id")
        if not data_url or not image_id:
            continue
        content.append({"type": "text", "text": f"Image ID: {image_id}"})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    response = call_openai_compatible_chat(
        model,
        messages=[
            {"role": "system", "content": MCQ_DATASET_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0,
    )

    message = get_openai_compatible_message_text(response)
    try:
        parsed = json.loads(strip_json_code_fence(message))
    except json.JSONDecodeError:
        return mcq

    repaired = normalize_mcq_dataset(parsed, image_lookup=image_lookup).get("mcqs", [])
    if repaired:
        return repaired[0]

    return mcq


def merge_mcq_datasets(datasets):
    merged_mcqs = []
    merged_contexts = []
    seen_questions = set()

    for dataset in datasets:
        for mcq in dataset.get("mcqs", []):
            question_key = re.sub(r"\s+", " ", mcq.get("question") or "").strip()
            if not question_key or question_key in seen_questions:
                continue
            seen_questions.add(question_key)
            merged_mcqs.append(mcq)

        for context in dataset.get("contexts", []):
            if context and context not in merged_contexts:
                merged_contexts.append(context)

    numbered_count = sum(
        1 for mcq in merged_mcqs
        if get_question_number_from_text(mcq.get("question") or "") is not None
    )
    if numbered_count >= max(2, len(merged_mcqs) // 2):
        merged_mcqs.sort(
            key=lambda mcq: (
                get_question_number_from_text(mcq.get("question") or "") is None,
                get_question_number_from_text(mcq.get("question") or "") or 10**9,
            )
        )

    return {
        "mcqs": merged_mcqs,
        "contexts": merged_contexts,
    }


def process_with_gemini(file_path, output_format):
    caps = get_openai_compat_capabilities()
    if not caps["vision_supported"]:
        raise RuntimeError(
            "The configured local OpenAI-compatible model does not support images. "
            "Set OPENAI_COMPAT_MODEL to a Gemini vision-capable model."
        )

    model = caps["vision_model"]
    page_images = get_document_page_images(file_path, dpi=200)

    if output_format == "json":
        page_datasets = []
        for page_number, image in page_images:
            page_text = extract_clean_text_with_gemini(image, page_number, model)
            page_dataset = transform_page_image_to_mcq_dataset_with_gemini(
                image,
                page_number,
                model,
                page_text=page_text,
            )
            expected_count = get_longest_consecutive_question_count(page_text)
            if expected_count and len(page_dataset.get("mcqs", [])) < expected_count:
                missing_dataset = extract_missing_mcqs_with_gemini(
                    image,
                    page_number,
                    page_text,
                    page_dataset,
                    model,
                )
                page_dataset = merge_mcq_datasets([page_dataset, missing_dataset])
            page_datasets.append(page_dataset)
        dataset = merge_mcq_datasets(page_datasets)
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'gemini_{uuid.uuid4().hex}.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)
        preview_text = json.dumps(dataset, ensure_ascii=False, indent=2)
    else:
        extracted_text = []
        for page_number, image in page_images:
            text = extract_clean_text_with_gemini(image, page_number, model)
            extracted_text.append({
                "page": page_number,
                "text": text or "",
            })

        full_text = "\n\n".join(page["text"] for page in extracted_text)

        if output_format == "txt":
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'gemini_{uuid.uuid4().hex}.txt')
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(full_text)
            preview_text = full_text
        elif output_format == "docx":
            from docx import Document
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'gemini_{uuid.uuid4().hex}.docx')
            doc = Document()
            doc.add_heading('Gemini Extracted Text', 0)
            for page_data in extracted_text:
                doc.add_heading(f'Page {page_data["page"]}', level=1)
                for line_text in page_data["text"].splitlines():
                    doc.add_paragraph(line_text)
            doc.save(output_path)
            preview_text = full_text
        else:
            raise ValueError("Gemini engine supports TXT, DOCX, and JSON output formats.")

    session['output_file'] = output_path

    return {
        'text': preview_text,
        'pages': len(page_images),
        'output_path': output_path,
    }

def configure_tesseract_command(pytesseract_module):
    # Use PATH first; if not found, try common Windows install locations.
    tesseract_on_path = shutil.which("tesseract")
    if tesseract_on_path:
        pytesseract_module.pytesseract.tesseract_cmd = tesseract_on_path
        return tesseract_on_path

    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                pytesseract_module.pytesseract.tesseract_cmd = candidate
                return candidate

    return None


def html_block_to_plain_text(content):
    if not content:
        return ""
    text = content
    text = re.sub(r'(?i)<br\\s*/?>', '\n', text)
    text = re.sub(r'(?i)</tr\\s*>', '\n', text)
    text = re.sub(r'(?i)</t[dh]\\s*>', '\t', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join([line for line in lines if line])


def create_positioned_docx_from_aistudio(layout_results, output_path):
    from docx import Document
    from docx.shared import Pt, Twips
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    # Keep page width standard Letter; scale all coordinates proportionally.
    target_page_width_twips = 12240
    max_page_height_twips = 30000

    doc = Document()
    first_page = True

    for page_result in layout_results:
        pruned = page_result.get('prunedResult', {}) or {}
        page_width = max(1, int(pruned.get('width') or 1))
        page_height = max(1, int(pruned.get('height') or 1))
        blocks = pruned.get('parsing_res_list') or []
        blocks = sorted(
            blocks,
            key=lambda b: b.get('block_order') if isinstance(b.get('block_order'), int) else 10**9
        )

        scale = target_page_width_twips / page_width
        page_height_twips = int(page_height * scale)
        if page_height_twips > max_page_height_twips:
            scale = max_page_height_twips / page_height
            page_height_twips = max_page_height_twips

        if first_page:
            section = doc.sections[0]
            first_page = False
        else:
            section = doc.add_section(start_type=1)

        section.page_width = Twips(target_page_width_twips)
        section.page_height = Twips(max(1, page_height_twips))
        section.left_margin = Twips(0)
        section.right_margin = Twips(0)
        section.top_margin = Twips(0)
        section.bottom_margin = Twips(0)

        for block in blocks:
            bbox = block.get('block_bbox')
            text = html_block_to_plain_text(block.get('block_content', ''))
            if not text or not isinstance(bbox, list) or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = bbox
            w = max(1, int((x2 - x1) * scale))
            h = max(1, int((y2 - y1) * scale))
            x = max(0, int(x1 * scale))
            y = max(0, int(y1 * scale))

            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0

            p_pr = p._p.get_or_add_pPr()
            frame_pr = OxmlElement('w:framePr')
            frame_pr.set(qn('w:w'), str(w))
            frame_pr.set(qn('w:h'), str(h))
            frame_pr.set(qn('w:x'), str(x))
            frame_pr.set(qn('w:y'), str(y))
            frame_pr.set(qn('w:hRule'), 'exact')
            frame_pr.set(qn('w:vAnchor'), 'page')
            frame_pr.set(qn('w:hAnchor'), 'page')
            frame_pr.set(qn('w:wrap'), 'none')
            p_pr.insert(0, frame_pr)

            run = p.add_run(text)
            run.font.name = 'Calibri'
            run.font.size = Pt(10)

    doc.save(output_path)


def process_with_aistudio(file_path, output_format):
    API_URL = "https://c7h8c1o6l62ej1ze.aistudio-app.com/layout-parsing"
    TOKEN = "eabce52d47f2eacb24c9335a1a0e6b195e335efe"

    with open(file_path, "rb") as file:
        file_bytes = file.read()
        file_data = base64.b64encode(file_bytes).decode("ascii")

    headers = {
        "Authorization": f"token {TOKEN}",
        "Content-Type": "application/json"
    }

    if is_pdf_file(file_path):
        file_type = 0
    elif is_image_file(file_path):
        file_type = 1
    else:
        raise ValueError("AI Studio supports PDF or image files only.")

    required_payload = {
        "file": file_data,
        "fileType": file_type,
    }

    optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    payload = {**required_payload, **optional_payload}

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=AISTUDIO_TIMEOUT_SECONDS)
    except requests.Timeout as exc:
        raise Exception(
            f"AI Studio request timed out after {int(AISTUDIO_TIMEOUT_SECONDS)} seconds. "
            "Try a smaller PDF/image, or increase AISTUDIO_TIMEOUT_SECONDS."
        ) from exc
    except requests.RequestException as exc:
        raise Exception(f"AI Studio request failed: {str(exc)}")

    if response.status_code != 200:
        raise Exception(f"API Error {response.status_code}: {response.text}")
    
    result = response.json()["result"]

    # Create a unique output directory for this job to extract everything
    job_id = uuid.uuid4().hex
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], f"aistudio_{job_id}")
    os.makedirs(output_dir, exist_ok=True)

    full_markdown_text = ""
    image_assets = []
    page_payloads = []
    full_page_assets = build_source_page_image_assets(file_path)
    pages = 0

    for i, res in enumerate(result.get("layoutParsingResults", [])):
        pages += 1
        page_assets = [
            asset for asset in full_page_assets
            if asset.get("id") == f"page_{pages}:full_page"
        ]
        md_filename = os.path.join(output_dir, f"doc_{i}.md")
        markdown = res.get("markdown", {}) or {}
        page_images = markdown.get("images", {}) or {}
        page_md = markdown.get("text", "")
        page_md_for_gemini = replace_markdown_image_sources_with_refs(page_md, pages, page_images)
        full_markdown_text += page_md_for_gemini + "\n\n"
        with open(md_filename, "w", encoding="utf-8") as md_file:
            md_file.write(page_md)
        
        for img_path, img_url in page_images.items():
            full_img_path = os.path.join(output_dir, img_path)
            os.makedirs(os.path.dirname(full_img_path), exist_ok=True)
            try:
                img_bytes = download_image_bytes(img_url)
                with open(full_img_path, "wb") as img_file:
                    img_file.write(img_bytes)
                image_assets.append({
                    "id": f"page_{pages}:{img_path}",
                    "path": img_path,
                    "data_url": image_bytes_to_data_url(img_bytes, img_path),
                })
                page_assets.append(image_assets[-1])
            except Exception:
                pass
        
        if "outputImages" in res:
            for img_name, img_url in res["outputImages"].items():
                try:
                    img_response = requests.get(img_url, timeout=60)
                    if img_response.status_code == 200:
                        filename = os.path.join(output_dir, f"{img_name}_{i}.jpg")
                        with open(filename, "wb") as f:
                            f.write(img_response.content)
                except:
                    pass

        page_payloads.append({
            "text": page_md_for_gemini,
            "assets": page_assets,
        })

    # Create zip archive of the directory
    zip_path_base = os.path.join(app.config['OUTPUT_FOLDER'], f"aistudio_{job_id}")
    shutil.make_archive(zip_path_base, 'zip', output_dir)
    
    if output_format == 'json':
        caps = get_openai_compat_capabilities()
        model = caps["vision_model"] or caps["text_model"]
        if caps["vision_supported"]:
            page_datasets = []
            for page_payload in page_payloads:
                page_text = page_payload.get("text") or ""
                page_assets_for_model = page_payload.get("assets") or []
                if not page_text.strip():
                    continue
                page_dataset = transform_aistudio_layout_to_mcq_dataset(page_text, page_assets_for_model, model)
                repaired_mcqs = []
                for mcq in page_dataset.get("mcqs", []):
                    if mcq.get("answer") is None:
                        mcq = repair_aistudio_mcq_with_page_image(
                            mcq,
                            page_text,
                            page_assets_for_model,
                            model,
                        )
                    repaired_mcqs.append(mcq)
                page_dataset["mcqs"] = repaired_mcqs
                page_datasets.append(page_dataset)
            dataset = merge_mcq_datasets(page_datasets)
        else:
            dataset = transform_ocr_text_to_mcq_dataset(full_markdown_text, model)
        final_output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"aistudio_{job_id}.json")
        with open(final_output_path, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)
    elif output_format == 'zip':
        final_output_path = zip_path_base + ".zip"
    elif output_format == 'txt':
        final_output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"aistudio_{job_id}.txt")
        with open(final_output_path, 'w', encoding='utf-8') as f:
            f.write(full_markdown_text)
    elif output_format == 'docx':
        final_output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"aistudio_{job_id}.docx")
        create_positioned_docx_from_aistudio(result.get("layoutParsingResults", []), final_output_path)
    else:
        final_output_path = zip_path_base + ".zip"

    # Store path of formatted file in session for download
    session['output_file'] = final_output_path
    
    # Optional cleanup of the unzipped folder
    try:
        shutil.rmtree(output_dir)
    except:
        pass

    return {
        'text': json.dumps(dataset, ensure_ascii=False, indent=2) if output_format == 'json' else full_markdown_text,
        'pages': pages,
        'output_path': final_output_path
    }


def reconstruct_layout(ocr_results):
    if not ocr_results:
        return ""
        
    boxes = []
    for res in ocr_results:
        if len(res) == 3:
            bounds, text, conf = res
            if not bounds or not isinstance(bounds, list) or len(bounds) != 4:
                continue
            
            try:
                min_x = min([p[0] for p in bounds])
                max_x = max([p[0] for p in bounds])
                min_y = min([p[1] for p in bounds])
                max_y = max([p[1] for p in bounds])
                
                # Approximate width per character
                char_width = (max_x - min_x) / max(1, len(text))
                
                boxes.append({
                    'min_x': min_x, 'max_x': max_x, 
                    'min_y': min_y, 'max_y': max_y,
                    'text': text, 'char_width': char_width
                })
            except Exception:
                pass

    if not boxes:
        return ""

    # Sort boxes by top y coordinate
    boxes.sort(key=lambda b: b['min_y'])

    lines = []
    current_line = []
    
    for box in boxes:
        if not current_line:
            current_line.append(box)
        else:
            # Check if this box belongs to the current line based on Y-overlap
            min_y_line = min([b['min_y'] for b in current_line])
            max_y_line = max([b['max_y'] for b in current_line])
            line_height = max(1, max_y_line - min_y_line)
            
            box_center_y = (box['min_y'] + box['max_y']) / 2
            
            # If the box center is within the current line vertical bounds, or very close
            if min_y_line <= box_center_y <= max_y_line or abs(box_center_y - (min_y_line + max_y_line)/2) < line_height * 0.5:
                current_line.append(box)
            else:
                lines.append(current_line)
                current_line = [box]
                
    if current_line:
        lines.append(current_line)
        
    # Build text preserving approximate horizontal gaps
    output = []
    for line in lines:
        line.sort(key=lambda b: b['min_x'])
        
        char_widths = [b['char_width'] for b in line if b['char_width'] > 0]
        avg_char_width = sum(char_widths) / len(char_widths) if char_widths else 10
        avg_char_width = max(1, avg_char_width)
        
        line_str = ""
        last_x = 0
        
        for i, box in enumerate(line):
            gap = box['min_x'] - last_x
            
            if i == 0:
                # Add initial indent relative to left margin
                if gap > avg_char_width * 2:
                    spaces = int(gap / avg_char_width / 1.5) 
                    line_str += " " * min(spaces, 20)
            else:
                if gap > avg_char_width * 0.8:
                    spaces = int(gap / avg_char_width)
                    line_str += " " * min(spaces, 40)
                else:
                    line_str += " "
                    
            line_str += box['text']
            last_x = box['max_x']
            
        output.append(line_str)
        
    return "\n".join(output)


def process_file_with_ocr(file_path, language, output_format, engine_name='easyocr'):
    if engine_name == 'easyocr':
        langs = get_easyocr_langs(language)
        reader = get_reader(langs)
    elif engine_name == 'tesseract':
        lang_map = {'english': 'eng', 'bengali': 'ben', 'both': 'eng+ben'}
        tess_lang = lang_map.get(language, 'eng')
    elif engine_name == 'openai_compatible':
        openai_caps = get_openai_compat_capabilities()

    extracted_text = []
    dpi = 200 if engine_name == 'openai_compatible' else 300
    page_images = get_document_page_images(file_path, dpi=dpi)

    for page_number, img in page_images:

        temp_img_path = os.path.join(app.config['OUTPUT_FOLDER'], f'temp_{uuid.uuid4().hex}.png')
        img.save(temp_img_path)

        try:
            if engine_name == 'easyocr':
                # Extract text using EasyOCR with bounding box details
                result = reader.readtext(temp_img_path, detail=1)
                text = reconstruct_layout(result)
            elif engine_name == 'tesseract':
                import pytesseract
                from pytesseract import Output
                if not configure_tesseract_command(pytesseract):
                    raise RuntimeError(
                        "Tesseract executable not found. Install Tesseract OCR "
                        "or add tesseract.exe to your PATH."
                    )
                data = pytesseract.image_to_data(temp_img_path, lang=tess_lang, output_type=Output.DICT)
                
                ocr_results = []
                n_boxes = len(data['text'])
                for i in range(n_boxes):
                    if int(data['conf'][i]) > -1:
                        val = data['text'][i].strip()
                        if val:
                            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                            bounds = [[x, y], [x+w, y], [x+w, y+h], [x, y+h]]
                            ocr_results.append((bounds, val, data['conf'][i]))
                text = reconstruct_layout(ocr_results)
            elif engine_name == 'openai_compatible':
                text_model = openai_caps['text_model']
                vision_model = openai_caps['vision_model']

                if vision_model:
                    try:
                        text = extract_text_with_openai_compatible(img, page_number, vision_model)
                    except Exception as vision_exc:
                        if should_fallback_openai_vision(vision_exc):
                            fallback_text = extract_text_with_local_ocr_fallback(img, language)
                            if OPENAI_COMPAT_ENABLE_POSTPROCESS:
                                try:
                                    text = cleanup_ocr_text_with_openai_compatible(fallback_text, text_model)
                                except Exception:
                                    text = fallback_text
                            else:
                                text = fallback_text
                        else:
                            raise
                else:
                    fallback_text = extract_text_with_local_ocr_fallback(img, language)
                    if OPENAI_COMPAT_ENABLE_POSTPROCESS:
                        try:
                            text = cleanup_ocr_text_with_openai_compatible(fallback_text, text_model)
                        except Exception:
                            text = fallback_text
                    else:
                        text = fallback_text
        except Exception as e:
            text = f"[OCR Error on page {page_number}: {str(e)}]"
        finally:
            if os.path.exists(temp_img_path):
                os.remove(temp_img_path)

        extracted_text.append({
            'page': page_number,
            'text': text or ""
        })

    full_text = "\n\n".join([page['text'] for page in extracted_text])

    if output_format == 'json':
        openai_caps = get_openai_compat_capabilities()
        dataset = transform_ocr_text_to_mcq_dataset(full_text, openai_caps['text_model'])
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{uuid.uuid4().hex}.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)
        preview_text = json.dumps(dataset, ensure_ascii=False, indent=2)
    elif output_format == 'txt':
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{uuid.uuid4().hex}.txt')
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(full_text)
        preview_text = full_text
    elif output_format == 'docx':
        from docx import Document
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{uuid.uuid4().hex}.docx')
        doc = Document()
        doc.add_heading('Extracted Text from PDF', 0)

        for page_data in extracted_text:
            doc.add_heading(f'Page {page_data["page"]}', level=1)
            for line_text in page_data['text'].split('\n'):
                p = doc.add_paragraph(line_text)
                for run in p.runs:
                    run.font.name = 'Courier New'

        doc.save(output_path)
        preview_text = full_text
    else:
        raise ValueError("Unsupported output format.")

    session['output_file'] = output_path

    return {
        'text': preview_text,
        'pages': len(extracted_text),
        'output_path': output_path
    }


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{uuid.uuid4().hex}_{filename}')
        file.save(filepath)
        session['uploaded_file'] = filepath
        session['original_filename'] = filename
        return jsonify({'success': True, 'filename': filename}), 200

    return jsonify({'error': 'Invalid file type. Upload a PDF or image file.'}), 400


@app.route('/process', methods=['POST'])
def process():
    if 'uploaded_file' not in session:
        return jsonify({'error': 'No file uploaded'}), 400

    engine = request.form.get('engine', 'easyocr')
    language = request.form.get('language', 'english')
    output_format = request.form.get('format', 'txt')

    try:
        if engine == 'aistudio':
            result = process_with_aistudio(session['uploaded_file'], output_format)
        elif engine == 'gemini':
            result = process_with_gemini(session['uploaded_file'], output_format)
        else:
            result = process_file_with_ocr(session['uploaded_file'], language, output_format, engine)
            
        return jsonify({
            'success': True,
            'text': result['text'],
            'pages': result['pages']
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/download/<format>')
def download(format):
    if 'output_file' not in session:
        return jsonify({'error': 'No file to download'}), 400

    output_path = session['output_file']

    if not os.path.exists(output_path):
        return jsonify({'error': 'File not found'}), 404

    original_name = session.get('original_filename', 'output')
    base_name = os.path.splitext(original_name)[0]

    if format == 'txt':
        return send_file(output_path, as_attachment=True, download_name=f'{base_name}.txt', mimetype='text/plain')
    elif format == 'docx':
        return send_file(output_path, as_attachment=True, download_name=f'{base_name}.docx',
                        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    elif format == 'json':
        return send_file(output_path, as_attachment=True, download_name=f'{base_name}.json', mimetype='application/json')
    elif format == 'zip':
        return send_file(output_path, as_attachment=True, download_name=f'{base_name}.zip', mimetype='application/zip')
    else:
        return jsonify({'error': 'Invalid format'}), 400


@app.route('/upload_to_db', methods=['POST'])
def upload_to_db():
    data = request.json
    if not data or 'mcqs' not in data:
        return jsonify({'error': 'Invalid JSON data'}), 400

    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor()
    except Error as e:
        return jsonify({'error': f'Database connection failed: {str(e)}'}), 500

    try:
        inserted_count = 0
        for item in data['mcqs']:
            # 1. Resolve Exam
            exam_name = item.get('exam') or 'Unknown Exam'
            cursor.execute('INSERT IGNORE INTO exams (name) VALUES (%s)', (exam_name,))
            cursor.execute('SELECT id FROM exams WHERE name = %s', (exam_name,))
            exam_id = cursor.fetchone()[0]

            # 2. Resolve Year
            year_val = item.get('year') or 'Unknown Year'
            cursor.execute('INSERT IGNORE INTO years (year) VALUES (%s)', (year_val,))
            cursor.execute('SELECT id FROM years WHERE year = %s', (year_val,))
            year_id = cursor.fetchone()[0]

            # 3. Insert Question
            cursor.execute('INSERT INTO questions (text) VALUES (%s)', (item['question'],))
            question_id = cursor.lastrowid

            # 4. Insert Options and collect IDs
            option_ids = []
            for opt in item['options']:
                cursor.execute('INSERT INTO options (text, type, image_base64) VALUES (%s, %s, %s)',
                             (opt.get('text'), opt.get('type', 'text'), opt.get('imageBase64')))
                option_ids.append(cursor.lastrowid)

            # 5. Insert MCQ
            answer_index = item.get('answer')
            answer_id = None
            if answer_index and 1 <= answer_index <= 4:
                answer_id = option_ids[answer_index - 1]

            cursor.execute('''
                INSERT INTO mcqs (question_id, option_ids, answer_index, answer_id, explanation, language, subject)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (
                question_id,
                json.dumps(option_ids),
                answer_index,
                answer_id,
                item.get('explanation'),
                item.get('language'),
                item.get('subject')
            ))
            mcq_id = cursor.lastrowid

            # 6. Insert into exam_questions mapping
            cursor.execute('''
                INSERT INTO exam_questions (exam_id, year_id, mcq_id)
                VALUES (%s, %s, %s)
            ''', (exam_id, year_id, mcq_id))

            inserted_count += 1

        conn.commit()
        return jsonify({'success': True, 'message': f'Successfully inserted {inserted_count} MCQs into MySQL database.'}), 200

    except Error as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


# Admin Panel Routes
@app.route('/admin')
def admin_dashboard():
    return render_template('admin/dashboard.html')

@app.route('/admin/exams')
def admin_exams():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM exams ORDER BY id DESC')
        exams = cursor.fetchall()
        return render_template('admin/exams.html', exams=exams)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin/years')
def admin_years():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM years ORDER BY id DESC')
        years = cursor.fetchall()
        return render_template('admin/years.html', years=years)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin/questions')
def admin_questions():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM questions ORDER BY id DESC')
        questions = cursor.fetchall()
        return render_template('admin/questions.html', questions=questions)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin/options')
def admin_options():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM options ORDER BY id DESC LIMIT 1000')
        options = cursor.fetchall()
        return render_template('admin/options.html', options=options)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin/mcqs')
def admin_mcqs():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        cursor.execute('''
            SELECT m.*, q.text as question_text
            FROM mcqs m
            JOIN questions q ON m.question_id = q.id
            ORDER BY m.id DESC
        ''')
        mcqs = cursor.fetchall()

        # Parse option_ids JSON for each MCQ
        for mcq in mcqs:
            if mcq['option_ids']:
                try:
                    mcq['option_ids_list'] = json.loads(mcq['option_ids'])
                except:
                    mcq['option_ids_list'] = []
            else:
                mcq['option_ids_list'] = []

        return render_template('admin/mcqs.html', mcqs=mcqs)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/admin/exam_questions')
def admin_exam_questions():
    try:
        conn = mysql.connector.connect(**app.config['DB_CONFIG'])
        cursor = conn.cursor(dictionary=True)
        # Proper joined view of exam questions
        cursor.execute('''
            SELECT
                eq.id as mapping_id,
                e.name as exam_name,
                y.year as exam_year,
                q.text as question_text,
                m.id as mcq_id,
                m.answer_index,
                m.explanation,
                m.language,
                m.subject,
                m.option_ids
            FROM exam_questions eq
            JOIN exams e ON eq.exam_id = e.id
            JOIN years y ON eq.year_id = y.id
            JOIN mcqs m ON eq.mcq_id = m.id
            JOIN questions q ON m.question_id = q.id
            ORDER BY eq.id DESC
        ''')
        data = cursor.fetchall()

        # For each item, fetch options detail
        for item in data:
            if item['option_ids']:
                try:
                    opt_ids = json.loads(item['option_ids'])
                except:
                    opt_ids = []

                if opt_ids:
                    # Filter out any non-integer IDs
                    opt_ids = [int(oid) for oid in opt_ids if str(oid).isdigit()]
                    if opt_ids:
                        placeholders = ', '.join(['%s'] * len(opt_ids))
                        cursor.execute(f'SELECT * FROM options WHERE id IN ({placeholders})', tuple(opt_ids))
                        options = cursor.fetchall()
                        # Reorder options based on opt_ids
                        opt_map = {o['id']: o for o in options}
                        item['options_detail'] = [opt_map[oid] for oid in opt_ids if oid in opt_map]
                    else:
                        item['options_detail'] = []
                else:
                    item['options_detail'] = []
            else:
                item['options_detail'] = []

        return render_template('admin/exam_questions.html', data=data)
    except Error as e:
        return str(e), 500
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()


@app.route('/cleanup', methods=['POST'])
def cleanup():
    if 'uploaded_file' in session:
        try:
            if os.path.exists(session['uploaded_file']):
                os.remove(session['uploaded_file'])
        except:
            pass
        session.pop('uploaded_file', None)

    if 'output_file' in session:
        try:
            if os.path.exists(session['output_file']):
                os.remove(session['output_file'])
        except:
            pass
        session.pop('output_file', None)

    return jsonify({'success': True}), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
