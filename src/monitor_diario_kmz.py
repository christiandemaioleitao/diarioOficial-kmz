"""
monitor_diario_kmz.py
─────────────────────
Main integration script.

Flow:
  1. Fetch latest Diário Oficial PDF from Goiânia portal.
  2. Skip if already processed (ultimo_diario.txt).
  3. Extract text from PDF.
  4. Use Gemini AI to identify Certidões de Remembramento / terrenos.
  5. For each terreno identified:
       a. Generate KMZ via kmz_generator.
       b. Validate KMZ integrity.
       c. Send Telegram notification with text + KMZ attachment + centroid.
  6. Update ultimo_diario.txt to avoid re-processing.

Environment variables required (see config.env):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_API_KEY,
  SUPABASE_URL, SUPABASE_KEY, URL_BASE_LOTES,
  + AI model keys (GEMINI_KEY, MISTRAL_KEY, etc.)
"""

import os
import io
import json
import datetime
import requests

from bs4 import BeautifulSoup
from pypdf import PdfReader
import google.generativeai as genai
from dotenv import load_dotenv

from src.kmz_generator import gerar_kmz_para_terreno, validar_kmz
from src.telegram_sender import send_message, send_terreno_notification

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────
URL_BASE   = "https://www.goiania.go.gov.br"
# Criando a data específica: ano, mês, dia
DATA_ALVO  = datetime.date(2026, 4, 27)
ANO_ALVO   = DATA_ALVO.year

URL_DIARIOS = (
    f"https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp"
    f"?ano={ANO_ALVO}"
)

GOOGLE_API_KEY   = os.environ.get("GOOGLE_API_KEY")
ULTIMO_DIARIO_TXT = "ultimo_diario.txt"

# ── Gemini setup ───────────────────────────────────────────────────────────────
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-2.5-flash")
else:
    gemini_model = None

# ── AI Prompt ─────────────────────────────────────────────────────────────────
PROMPT_IA = """
Analise o texto do Diário Oficial de Goiânia abaixo.
Identifique TODAS as 'Certidões de Remembramento' ou terrenos mencionados.

Para cada um, extraia:
1. interessado  — Nome completo do interessado
2. local        — Endereço/Localização do imóvel (bairro, quadra, lote(s))
3. decisao      — Resumo da decisão
4. endereco_kmz — Endereço estruturado para geração de KMZ
                  Formato: "Bairro X, Quadra Y, Lote(s) Z"

Retorne um JSON válido com uma lista de objetos:
[
  {
    "interessado": "Nome",
    "local": "Endereço completo",
    "decisao": "Resumo",
    "endereco_kmz": "Bairro X, Quadra Y, Lote Z"
  }
]

Se não encontrar nada, retorne: []
Retorne APENAS o JSON, sem texto adicional.
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extrair_texto_pdf(pdf_content: bytes) -> str:
    """Extract text from a PDF byte stream page by page."""
    texto = ""
    try:
        reader = PdfReader(io.BytesIO(pdf_content))
        for page in reader.pages:
            t = page.extract_text()
            if t:
                texto += t + "\n"
    except Exception as exc:
        print(f"⚠️  Erro na extração do PDF: {exc}")
    return texto


def _ler_ultimo_diario() -> str:
    """Read the last-processed diary identifier from disk."""
    if os.path.exists(ULTIMO_DIARIO_TXT):
        with open(ULTIMO_DIARIO_TXT, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


def _salvar_ultimo_diario(identificador: str) -> None:
    """Persist the current diary identifier to disk."""
    with open(ULTIMO_DIARIO_TXT, "w", encoding="utf-8") as fh:
        fh.write(identificador)


def _buscar_link_pdf() -> str | None:
    """Scrape the Goiânia portal and return the most recent PDF URL."""
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(URL_DIARIOS, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if "pdf" in href or "exibe" in href:
            return requests.compat.urljoin(URL_BASE, a["href"])
    return None


def _analisar_com_gemini(texto: str) -> list[dict]:
    """
    Send PDF text to Gemini and parse the returned JSON list of terrenos.
    Returns empty list on failure.
    """
    if gemini_model is None:
        print("⚠️  GOOGLE_API_KEY não configurado. Pulando análise por IA.")
        return []

    try:
        response = gemini_model.generate_content(f"{PROMPT_IA}\n\n{texto}")
        raw = response.text.strip()
        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError as exc:
        print(f"⚠️  Resposta da IA não é JSON válido: {exc}")
    except Exception as exc:
        print(f"⚠️  Erro ao chamar Gemini: {exc}")
    return []


# ── Main pipeline ──────────────────────────────────────────────────────────────

def processar_terreno(terreno: dict, link_pdf: str) -> None:
    """
    Process a single terreno: generate KMZ, validate, send to Telegram.
    Errors are caught and reported but do not halt processing of other terrenos.
    """
    interessado  = terreno.get("interessado", "Desconhecido")
    endereco_kmz = terreno.get("endereco_kmz", "")
    terreno["link_pdf"] = link_pdf

    print(f"\n  📌 Processando: {interessado}")
    print(f"     Endereço KMZ: {endereco_kmz}")

    kmz_path  = None
    centroide = None

    # ── Generate KMZ ─────────────────────────────────────────────────────────
    if endereco_kmz:
        try:
            kmz_path, centroide = gerar_kmz_para_terreno(endereco_kmz)
            if not validar_kmz(kmz_path):
                print("  ⚠️  KMZ gerado mas falhou na validação. Enviando sem KMZ.")
                kmz_path = None
        except Exception as exc:
            print(f"  ❌ Erro ao gerar KMZ: {exc}")
            send_message(
                f"⚠️ <b>KMZ não gerado para:</b> <i>{interessado}</i>\n"
                f"Erro: {exc}"
            )

    # ── Send to Telegram ──────────────────────────────────────────────────────
    ok = send_terreno_notification(
        terreno=terreno,
        kmz_path=kmz_path,
        centroide=centroide,
    )

    # ── Cleanup KMZ file ──────────────────────────────────────────────────────
    if kmz_path and os.path.exists(kmz_path):
        try:
            os.remove(kmz_path)
        except OSError:
            pass  # Non-critical

    status = "✅" if ok else "⚠️ (parcial)"
    print(f"  {status} Notificação enviada para {interessado}")


def main() -> None:
    """Entry point: fetch Diário Oficial, analyse, generate KMZs, notify Telegram."""
    print(f"🔍 Monitorando Diário Oficial de Goiânia — {ANO_ATUAL}")

    try:
        # ── Find latest PDF ───────────────────────────────────────────────────
        link_pdf = _buscar_link_pdf()
        if not link_pdf:
            send_message(f"❌ Nenhum PDF encontrado na página de {ANO_ATUAL}.")
            return

        # ── Dedup check ───────────────────────────────────────────────────────
        ultimo = _ler_ultimo_diario()
        if link_pdf == ultimo:
            print(f"✅ Diário já processado: {link_pdf}")
            return

        print(f"📥 Baixando: {link_pdf}")
        headers  = {"User-Agent": "Mozilla/5.0"}
        pdf_resp = requests.get(link_pdf, headers=headers, timeout=60)
        pdf_resp.raise_for_status()

        # ── Extract text ──────────────────────────────────────────────────────
        texto = _extrair_texto_pdf(pdf_resp.content)
        if not texto.strip():
            send_message(
                f"⚠️ PDF encontrado mas sem texto extraível.\n"
                f"🔗 <a href='{link_pdf}'>Ver PDF</a>"
            )
            return

        # ── AI analysis ───────────────────────────────────────────────────────
        print("🤖 Analisando com Gemini...")
        terrenos = _analisar_com_gemini(texto)

        if not terrenos:
            send_message(
                f"🏛️ <b>Diário Oficial ({ANO_ATUAL})</b>\n"
                f"Nenhum remembramento/terreno encontrado hoje.\n"
                f"🔗 <a href='{link_pdf}'>Ver PDF</a>"
            )
            _salvar_ultimo_diario(link_pdf)
            return

        # ── Process each terreno ──────────────────────────────────────────────
        send_message(
            f"🏛️ <b>Diário Oficial ({ANO_ATUAL})</b>\n"
            f"Encontrado(s) <b>{len(terrenos)}</b> registro(s).\n"
            f"🔗 <a href='{link_pdf}'>Ver PDF</a>\n"
            f"Gerando KMZs e notificando..."
        )

        for terreno in terrenos:
            processar_terreno(terreno, link_pdf)

        # ── Mark as processed ─────────────────────────────────────────────────
        _salvar_ultimo_diario(link_pdf)
        print(f"\n✅ Pipeline concluído. {len(terrenos)} terreno(s) processado(s).")

    except Exception as exc:
        erro = f"❌ Erro crítico no script:\n{exc}"
        print(erro)
        send_message(erro)


if __name__ == "__main__":
    required_vars = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_API_KEY"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        print(f"❌ Variáveis de ambiente faltando: {missing}")
        print("Configure as variáveis em .env ou nos Secrets do GitHub.")
    else:
        main()
