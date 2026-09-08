"""Windows: pip-installed CUDA DLLs live in site-packages and are NOT on the DLL
search path, so ctranslate2 fails with 'cublas64_12.dll is not found'.

add_dll_directory() alone is NOT sufficient — ctranslate2 delay-loads cublas via
the standard Windows search order, which consults PATH. Prepend to PATH too.
Call enable() BEFORE importing ctranslate2 / faster_whisper.
"""
import os
import sys
from pathlib import Path


def enable() -> list[str]:
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = []
    for d in sorted(base.glob("*/bin")) if base.exists() else []:
        if any(d.glob("*.dll")):
            os.add_dll_directory(str(d))
            dirs.append(str(d))
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
    return [Path(d).parent.name for d in dirs]
