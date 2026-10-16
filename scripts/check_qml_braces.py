"""Rough structural check of a QML file: balanced brackets and the nesting depth at each section.

No QML parser is available outside Cura, so this catches the typical slip after restructuring
(a missing or extra closing brace). Usage: python scripts/check_qml_braces.py <file.qml>
"""

import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    source = handle.read()
stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', source)  # string literals
stripped = re.sub(r"//[^\n]*", "", stripped)  # line comments

for opening, closing in ("{}", "()", "[]"):
    print(f"{opening}{closing}  {stripped.count(opening)} / {stripped.count(closing)}")

depth = 0
original = source.splitlines()
for number, line in enumerate(stripped.splitlines(), 1):
    depth += line.count("{") - line.count("}")
    if "// ----" in original[number - 1]:
        print(f"line {number:4d}  depth after: {depth}  {original[number - 1].strip()}")
print("final depth:", depth)
sys.exit(0 if depth == 0 else 1)
