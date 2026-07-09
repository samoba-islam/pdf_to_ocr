import sys

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_func = '''
def parse_json_llm_response(message):
    import ast
    import re
    import json
    import logging
    text = strip_json_code_fence(message)
    try:
        return json.loads(text)
    except Exception as e:
        # Fallback 1: Fix trailing commas
        try:
            text_no_trailing = re.sub(r',\\s*([\\]}])', r'\\1', text)
            return json.loads(text_no_trailing)
        except Exception:
            pass
            
        # Fallback 2: Try ast.literal_eval in case LLM output a Python dictionary with single quotes
        try:
            if text.startswith('{') or text.startswith('['):
                return ast.literal_eval(text)
        except Exception:
            pass
            
        # Fallback 3: Return empty dataset instead of crashing
        logging.warning(f"Failed to parse LLM JSON. Returning empty dataset. Raw text: {text[:200]}...")
        return {"mcqs": [], "contexts": []}
'''

# Insert new function after strip_json_code_fence
parts = content.split('def extract_option_number_from_text(text, options):')
if len(parts) == 2:
    content = parts[0] + new_func + '\n\ndef extract_option_number_from_text(text, options):' + parts[1]
    
    # Replace json.loads(strip_json_code_fence(content)) -> parse_json_llm_response(content)
    content = content.replace('json.loads(strip_json_code_fence(content))', 'parse_json_llm_response(content)')
    # Replace json.loads(strip_json_code_fence(message)) -> parse_json_llm_response(message)
    content = content.replace('json.loads(strip_json_code_fence(message))', 'parse_json_llm_response(message)')
    
    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Successfully modified app.py!')
else:
    print('Failed to find insertion point!')
