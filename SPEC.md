# PDF to Text/DOCX Converter with OCR Support

## Project Overview
- **Project Name**: pdf_to_ocr
- **Type**: Web-based Python application
- **Core Functionality**: Extract text from PDF files using OCR technology, supporting Bengali and English languages, with export to TXT or DOCX formats
- **Target Users**: Users who need to extract text from scanned PDF documents or images in PDF format

## Technical Stack
- **Backend**: Python Flask
- **OCR Engine**: Tesseract OCR with pytesseract
- **PDF Processing**: pdf2image (for converting PDF pages to images)
- **DOCX Generation**: python-docx
- **Frontend**: HTML5, CSS3, JavaScript (vanilla)

## Functionality Specification

### Core Features
1. **PDF File Upload**
   - Accept PDF files via web interface
   - Display upload progress
   - Validate file type (PDF or images)
   - Maximum file size: 1GB

2. **Job Control & Range Selection**
   - Custom page range support (e.g., "1-5, 8, 11-13")
   - Stop (Pause), Resume, and Cancel functionality during processing

3. **Language Selection**
   - Support for Bengali (ben), English (eng), or Both
   - Tesseract OCR language packs required

3. **OCR Processing**
   - Convert PDF pages to images
   - Apply OCR on each page
   - Handle multi-page PDFs
   - Display extracted text preview

4. **Export Options**
   - Export to plain text (.txt)
   - Export to Word document (.docx)
   - Automatic download after processing

5. **Admin Panel & Maintenance**
   - Dashboard for managing extracted MCQ data
   - Settings section for system maintenance
   - Manual cleanup of Uploads and Outputs folders with disk usage indicators

### User Interface
1. **Upload Section**
   - Drag-and-drop file upload area
   - File browser fallback
   - Display selected filename

2. **Settings Section**
   - Language dropdown (English, Bengali, Both)
   - Output format selection (TXT, DOCX)

3. **Processing Section**
   - Progress indicator during OCR
   - Page-by-page processing status

4. **Result Section**
   - Preview of extracted text
   - Download buttons for both formats

### API Endpoints
- `GET /` - Serve main page
- `POST /upload` - Handle PDF upload
- `POST /process` - Process PDF with OCR
- `GET /download/<format>` - Download extracted text

## Project Structure
```
pdf_to_ocr/
├── app.py              # Main Flask application
├── templates/
│   └── index.html      # Main HTML template
├── static/
│   └── style.css       # Custom styles
├── requirements.txt    # Python dependencies
└── README.md          # Setup instructions
```

## Acceptance Criteria
1. User can upload a PDF file via drag-drop or file browser
2. User can select language: English, Bengali, or Both
3. User can select output format: TXT or DOCX
4. OCR extracts text from scanned/image PDFs
5. Progress is shown during processing
6. Extracted text is displayed in preview
7. User can download the result in selected format
8. Application handles multi-page PDFs correctly
9. Application provides error messages for invalid files
