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

# FIX: credentials are read inside each function at call time, NOT at module
# import time.  The old pattern built TELEGRAM_API = "…/bot{token}" at import,
# which produced "…/botNone" when the env var wasn't loaded yet — causing every
# API call to silently fail with a 404 that was never logged properly.


def _get_creds() -> tuple[str | None, str | None]:
    """Return (token, chat_id) read from env at call time."""
    return (
        os.environ.get("TELEGRAM_TOKEN"),
        os.environ.get("TELEGRAM_CHAT_ID"),
    )


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a text message to the configured Telegram chat.
    Long messages are automatically split into ≤4000-char chunks.

    Returns True on success, False on failure.
    """
    token, chat_id = _get_creds()
    if not token or not chat_id:
        print("⚠️  TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não configurados.")
        return False
    if not text:
        return False

    api_base = f"https://api.telegram.org/bot{token}"
    url      = f"{api_base}/sendMessage"
    chunks   = [text[i:i + 4000] for i in range(0, len(text), 4000)]

    success = True
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if not resp.ok:
                # FIX: log actual Telegram error body, not just exception
                print(f"❌ Telegram sendMessage {resp.status_code}: {resp.text[:300]}")
                success = False
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
    token, chat_id = _get_creds()
    if not token or not chat_id:
        print("⚠️  TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não configurados.")
        return False

    if not os.path.exists(file_path):
        print(f"❌ Arquivo não encontrado: {file_path}")
        return False

    api_base = f"https://api.telegram.org/bot{token}"
    url  = f"{api_base}/sendDocument"
    data = {
        "chat_id": chat_id,
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
        if not resp.ok:
            # FIX: log actual Telegram error body
            print(f"❌ Telegram sendDocument {resp.status_code}: {resp.text[:300]}")
            return False
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
