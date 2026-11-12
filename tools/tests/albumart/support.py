"""Extract production functions unchanged; deterministic adapters live in tests."""
from pathlib import Path
import os
import re
import shlex
import subprocess

ROOT = Path(os.environ["ART_TEST_ROOT"])
OUT = Path(os.environ["ART_TEST_OUT"])
SUPPORT = Path(__file__).resolve().parent

def function(path, name):
    text = (ROOT / path).read_text()
    match = re.search(r"(?m)^[\w *]+\b" + name + r"\s*\([^;{}]*\)\s*\{", text)
    if not match:
        raise ValueError(name)
    start, brace, depth = match.start(), match.end() - 1, 0
    pattern = r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[{}]"
    for token in re.finditer(pattern, text[brace:], re.S):
        if token.group() == "{":
            depth += 1
        if token.group() == "}":
            depth -= 1
            if depth == 0:
                return '\n#line %d "%s"\n%s\n' % (
                    text[:start].count("\n") + 1, path, text[start:brace + token.end()])
    raise ValueError(name)

def run(args, name):
    p = subprocess.run(list(map(str, args)), cwd=OUT, capture_output=True, text=True)
    (OUT / (name + ".log")).write_text(shlex.join(list(map(str,args))) + "\n" + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(name + "\n" + p.stdout + p.stderr)
    return p.stdout

def build(name, code):
    source = OUT / (name + ".c")
    source.write_text((SUPPORT / "host_preamble.h").read_text() + code)
    run([os.environ.get("CC", "cc"), "-std=gnu11", "-g", "-O1", "-fno-pie", "-no-pie",
         "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
         source, "-o", OUT / name], "compile-" + name)
    return OUT / name
