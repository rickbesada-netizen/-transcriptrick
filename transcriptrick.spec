# PyInstaller build for the Windows app.  Run: pyinstaller --noconfirm transcriptrick.spec
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = [("app/static", "app/static")]
binaries, hiddenimports = [], ["app.main", "app.worker", "app.downloads", "app.updater"]
for pkg in ("faster_whisper", "ctranslate2", "av", "imageio_ffmpeg", "yt_dlp", "yt_dlp_ejs",
            "youtube_transcript_api", "tokenizers", "onnxruntime", "huggingface_hub", "uvicorn", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
import deno, os
_deno = deno.find_deno_bin()
assert os.path.exists(_deno), "Deno binary missing: " + _deno
binaries.append((_deno, "deno_bin"))
for dist in ("yt-dlp", "youtube-transcript-api", "faster-whisper", "fastapi", "uvicorn", "httpx", "yt-dlp-ejs"):
    datas += copy_metadata(dist)

a = Analysis(["launcher.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Transcriptrick", console=False,
          icon="packaging/transcriptrick.ico", version="packaging/version.txt")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Transcriptrick")
