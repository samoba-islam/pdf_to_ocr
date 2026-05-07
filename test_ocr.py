import subprocess

cmd = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", "--list-langs"]
result = subprocess.run(cmd, capture_output=True, text=True)
print("stdout:", result.stdout)
print("stderr:", result.stderr)
print("returncode:", result.returncode)