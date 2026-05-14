"""
telegram_sender.py
──────────────────
Wrapper for Telegram Bot API.
Supports sending text messages and document (file) attachments.

All credentials loaded from environment variables.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def _check_env() -> bool:
    """Return False and print warning if token/chat_id are missing."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não configurados.")
        return False
    return True


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a text message to the configured Telegram chat.
    Long messages are automatically split into ≤4000-char chunks.

    Returns True on success, False on failure.
    """
    if not _check_env() or not text:
        return False

    url = f"{TELEGRAM_API}/sendMessage"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]

    success = True
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            print(f"❌ Erro ao enviar mensagem Telegram: {exc}")
            success = False

    return success


def send_document(
    file_path: str,
    caption: str = "",
    parse_mode: str = "Markdown",
) -> bool:
    """
    Send a document (e.g. KMZ file) to the configured Telegram chat.

    Args:
        file_path:  Absolute or relative path to the file to send.
        caption:    Optional caption shown below the file (≤1024 chars).
        parse_mode: 'Markdown' or 'HTML' for caption formatting.

    Returns True on success, False on failure.
    """
    if not _check_env():
        return False

    if not os.path.exists(file_path):
        print(f"❌ Arquivo não encontrado: {file_path}")
        return False

    url = f"{TELEGRAM_API}/sendDocument"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "caption": caption[:1024],
        "parse_mode": parse_mode,
    }

    try:
        with open(file_path, "rb") as fh:
            resp = requests.post(
                url,
                data=data,
                files={"document": fh},
                timeout=60,
            )
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"❌ Erro ao enviar documento Telegram: {exc}")
        return False


def send_terreno_notification(
    terreno: dict,
    kmz_path: str | None = None,
    centroide: tuple[float, float] | None = None,
) -> bool:
    """
    High-level helper: send a full terreno notification.

    Sends:
      1. A formatted HTML message with terreno details + centroid coords.
      2. The KMZ file as a document attachment (if kmz_path provided).

    Args:
        terreno:   Dict with keys: interessado, local, decisao, link_pdf (optional).
        kmz_path:  Path to the generated KMZ file (optional).
        centroide: (lat, lon) tuple (optional).

    Returns True if all sends succeeded.
    """
    # ── Build text message ──────────────────────────────────────────────
    lat_lon_str = (
        f"\n🗺️ <b>Centróide:</b> <code>{centroide[0]:.6f},{centroide[1]:.6f}</code>"
        if centroide
        else ""
    )

    msg = (
        f"🏢 <b>Interessado:</b> <i>{terreno.get('interessado', 'N/D')}</i>\n"
        f"📍 <b>Local:</b> <i>{terreno.get('local', 'N/D')}</i>\n"
        f"📝 <b>Decisão:</b> <i>{terreno.get('decisao', 'N/D')}</i>"
        f"{lat_lon_str}"
    )

    if terreno.get("link_pdf"):
        msg += f"\n🔗 <a href='{terreno['link_pdf']}'>Ver Diário Oficial</a>"

    ok_msg = send_message(msg)

    # ── Send KMZ document ───────────────────────────────────────────────
    ok_doc = True
    if kmz_path:
        caption = "📦 Arquivo KMZ do terreno"
        if centroide:
            caption += f"\n📍 `{centroide[0]:.6f},{centroide[1]:.6f}`"
        ok_doc = send_document(kmz_path, caption=caption)

    return ok_msg and ok_doc
