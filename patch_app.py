import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update ai_providers table definition
content = content.replace(
    "type ENUM('openai', 'gemini', 'ollama', 'aistudio', 'local') NOT NULL",
    "type ENUM('openai', 'gemini', 'ollama', 'aistudio', 'local', 'anthropic') NOT NULL"
)

# 2. Add Anthropic to default_providers
anthropic_provider = "\n                ('Anthropic API', 'anthropic', 'https://api.anthropic.com', ''),"
if "'Anthropic API'" not in content:
    content = content.replace(
        "('Gemini API', 'gemini', '', ''),",
        "('Gemini API', 'gemini', '', '')," + anthropic_provider
    )

# 3. Add Anthropic to default_engines
anthropic_engine = "\n            ('anthropic', 'Anthropic Claude (Cloud - Vision OCR & Structure)', 'Vision-based OCR using Anthropic Claude models.', True, 6),"
if "'anthropic', 'Anthropic Claude" not in content:
    content = content.replace(
        "('aistudio', 'AI Studio Layout API (Cloud - Layout & Images)', 'Advanced cloud layout parsing and image extraction.', True, 5)",
        "('aistudio', 'AI Studio Layout API (Cloud - Layout & Images)', 'Advanced cloud layout parsing and image extraction.', True, 5)," + anthropic_engine
    )

# 4. Add Anthropic to default settings
anthropic_settings = """
            ('ANTHROPIC_API_KEY', '', 'API Key for Anthropic Claude models', 'anthropic'),
            ('ANTHROPIC_MODEL', 'claude-3-5-sonnet-latest', 'Primary model for Anthropic OCR', 'anthropic'),
            ('ANTHROPIC_TIMEOUT_SECONDS', '240', 'Timeout for Anthropic requests (seconds)', 'anthropic'),"""
if "ANTHROPIC_API_KEY" not in content:
    content = content.replace(
        "('AISTUDIO_MAX_GEMINI_IMAGES', '80', 'Maximum images to send to Gemini per page', 'aistudio'),",
        "('AISTUDIO_MAX_GEMINI_IMAGES', '80', 'Maximum images to send to Gemini per page', 'aistudio')," + anthropic_settings
    )

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Basic DB schemas patched.")
