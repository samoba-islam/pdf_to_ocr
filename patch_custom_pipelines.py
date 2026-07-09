import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Patch perform_ocr_with_provider
if "elif p_type in ['openai', 'ollama', 'gemini', 'anthropic']:" not in content:
    content = content.replace(
        "elif p_type in ['openai', 'ollama', 'gemini']:",
        "elif p_type in ['openai', 'ollama', 'gemini', 'anthropic']:"
    )

if "elif p_type == 'anthropic':" not in content and "elif p_type == 'gemini':" in content:
    replacement = """        if p_type == 'openai':
            response = call_custom_openai_api(provider['base_url'], provider['api_key'], model or 'gpt-4o', messages)
            return response
        elif p_type == 'anthropic':
            return call_anthropic_api(model or 'claude-3-5-sonnet-latest', messages)"""
    content = re.sub(r"        if p_type == 'openai':\s+response = call_custom_openai_api[^}]+?return response", replacement, content, count=1)


# 2. Patch structure_text_with_provider
if "elif p_type == 'anthropic':\n            content = call_anthropic_api" not in content:
    replacement2 = """        if p_type == 'openai':
            content = call_custom_openai_api(provider['base_url'], provider['api_key'], model or 'gpt-4o', messages)
        elif p_type == 'anthropic':
            content = call_anthropic_api(model or 'claude-3-5-sonnet-latest', messages)"""
    content = re.sub(r"        if p_type == 'openai':\s+content = call_custom_openai_api[^\n]+", replacement2, content, count=1)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Custom pipelines patched.")
