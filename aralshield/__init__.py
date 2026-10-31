"""AralShield «Тұз дауылы» — раннее предупреждение о солёно-пылевых бурях Приаралья."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

__version__ = "1.0.0"


def _sha256(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _ascii_ca_bundle() -> None:
    """GDAL/curl на Windows не принимает путь к сертификатам с кириллицей
    (например, в профиле пользователя с кириллическим именем). Копируем пакет корневых
    сертификатов certifi в каталог с ASCII-путём и указываем его только GDAL
    (GDAL_CURL_CA_BUNDLE). Копия сверяется по SHA-256 и заменяется при расхождении;
    CURL_CA_BUNDLE (его читает и requests) задаётся лишь если не задан пользователем."""
    if os.name != "nt" or os.environ.get("GDAL_CURL_CA_BUNDLE"):
        return
    try:
        import certifi
    except ImportError:
        return
    src = Path(certifi.where())
    if str(src).isascii():
        os.environ["GDAL_CURL_CA_BUNDLE"] = str(src)
        os.environ.setdefault("CURL_CA_BUNDLE", str(src))
        return
    want = _sha256(src)
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "aralshield" if os.environ.get("LOCALAPPDATA") else None,
                  Path(tempfile.gettempdir()) / "aralshield",
                  Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "aralshield"]
    for d in candidates:
        if d is None or not str(d).isascii():
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            dst = d / "cacert.pem"
            if _sha256(dst) != want:  # не доверяем чужому файлу того же размера
                tmp = d / f"cacert.{os.getpid()}.tmp"
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
            if _sha256(dst) != want:
                continue
            os.environ["GDAL_CURL_CA_BUNDLE"] = str(dst)
            os.environ.setdefault("CURL_CA_BUNDLE", str(dst))
            return
        except OSError:
            continue


def _load_dotenv() -> None:
    """Читает .env в корне проекта (KEY=VALUE); уже заданные переменные не перезаписывает.
    ARALSHIELD_SKIP_DOTENV=1 (тесты) — не читать: боевые секреты не попадают в тесты."""
    if os.environ.get("ARALSHIELD_SKIP_DOTENV") == "1":
        return
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
_ascii_ca_bundle()
