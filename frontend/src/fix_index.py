import io
with io.open(r"d:\MACOV\APEX\frontend\src\index.css", "wb") as f:
    f.write(b"@import 'tailwindcss';\nhtml, body, #root { margin: 0; padding: 0; height: 100%; width: 100%; }\n")
