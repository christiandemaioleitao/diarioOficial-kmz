"""
monitor_diario_kmz.py
─────────────────────
Main integration script.

Flow:
  1. Fetch Diário Oficial PDF from Goiânia portal (today or DATA_OVERRIDE).
  2. Skip if already processed (ultimo_diario.txt) — unless FORCE_REPROCESS=1.
  3. Extract text from PDF.
  4. Use Gemini AI to identify ALL certidão types (Limites, Confrontações,
     Desmembramento, Remembramento, Localização, etc.).
  5. For each terreno identified:
       a. Generate KMZ via kmz_generator.
       b. Validate KMZ integrity.
       c. Send Telegram notification with text + KMZ attachment + centroid.
  6. Update ultimo_diario.txt to avoid re-processing.

Environment variables required (see config.env):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_API_KEY,
  SUPABASE_URL, SUPABASE_KEY, URL_BASE_LOTES,
  + AI model keys (GEMINI_KEY, MISTRAL_KEY, etc.)

Testing / override variables:
  DATA_OVERRIDE=YYYY-MM-DD   — process a specific date's Diário instead of today
  FORCE_REPROCESS=1          — skip the dedup check (reprocess even if seen before)
"""

import os
import io
import json
import datetime
import traceback
import requests

from bs4 import BeautifulSoup
from pypdf import PdfReader
import google.generativeai as genai
from dotenv import load_dotenv

from src.kmz_generator import gerar_kmz_para_terreno, validar_kmz
from src.telegram_sender import send_message, send_terreno_notification

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────
URL_BASE = "https://www.goiania.go.gov.br"

# Support DATA_OVERRIDE=YYYY-MM-DD for testing with specific dates
_DATA_OVERRIDE = os.environ.get("DATA_OVERRIDE", "").strip()
if _DATA_OVERRIDE:
    try:
        _DATA_TARGET = datetime.date.fromisoformat(_DATA_OVERRIDE)
        print(f"🗓️  DATA_OVERRIDE ativo: {_DATA_OVERRIDE}")
    except ValueError:
        print(f"⚠️  DATA_OVERRIDE inválido '{_DATA_OVERRIDE}'. Usando data de hoje.")
        _DATA_TARGET = datetime.date.today()
else:
    _DATA_TARGET = datetime.date.today()

ANO_ATUAL = _DATA_TARGET.year

URL_DIARIOS = (
    f"https://www.goiania.go.gov.br/shtml//portal/casacivil/lista_diarios.asp"
    f"?ano={ANO_ATUAL}"
)

GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY")
ULTIMO_DIARIO_TXT = "ultimo_diario.txt"
FORCE_REPROCESS   = os.environ.get("FORCE_REPROCESS", "0").strip() == "1"

# ── Gemini setup ───────────────────────────────────────────────────────────────
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    # FIX: use gemini-1.5-flash (stable GA release, not preview)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    gemini_model = None

# ── AI Prompt ─────────────────────────────────────────────────────────────────
# FIX: expanded beyond "Remembramento" to cover ALL certidão types found in the
#      Diário Oficial de Goiânia (Limites e Confrontações, Desmembramento, etc.)
PROMPT_IA = """
Analise o texto do Diário Oficial de Goiânia abaixo.

Identifique TODAS as certidões imobiliárias, incluindo (mas não limitado a):
- Certidão de Limites e Confrontações
- Certidão de Limites e Confrontações Sem Demarcação
- Certidão de Remembramento
- Certidão de Desmembramento
- Certidão de Localização de Área
- Certidão de Regularização
- Qualquer outra certidão relacionada a imóveis, terrenos ou lotes

Para cada certidão encontrada, extraia:
1. interessado  — Nome completo do interessado
2. local        — Endereço/localização (bairro, quadra, lote(s))
3. decisao      — Tipo da certidão + resumo da decisão
4. endereco_kmz — Endereço estruturado para geração de KMZ
                  Formato: "BAIRRO, Quadra Y, Lote(s) Z"
                  Use os valores exatos dos campos BAIRRO, QUADRA e LOTE(S) do texto.

Exemplo de bloco no texto:
  CERTIDÃO Nº 530/2026
  CERTIDÃO DE LIMITES E CONFRONTAÇÕES SEM DEMARCAÇÃO
  INTERESSADO LUZIA LOURENÇO DE PAULA
  INSCRIÇÃO IPTU 401.032.0043.007-3
  ENDEREÇO
  QUADRA 83 LOTE(S) 2/38 BAIRRO SETOR CENTRAL

  → interessado: "LUZIA LOURENÇO DE PAULA"
  → local: "QUADRA 83, LOTE(S) 2/38, SETOR CENTRAL"
  → decisao: "Certidão de Limites e Confrontações Sem Demarcação"
  → endereco_kmz: "SETOR CENTRAL, Quadra 83, Lote(s) 2/38"

Retorne um JSON válido com uma lista de objetos:
[
  {
    "interessado": "Nome",
    "local": "Endereço completo",
    "decisao": "Tipo da certidão + resumo",
    "endereco_kmz": "BAIRRO, Quadra Y, Lote(s) Z"
  }
]

Se não encontrar NENHUMA certidão imobiliária, retorne: []
Retorne APENAS o JSON, sem texto adicional, sem blocos markdown.
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
    """
    Scrape the Goiânia portal and return the PDF URL.
    When DATA_OVERRIDE is set, attempts to find the PDF for that specific date.
    Falls back to the most recent PDF if the specific date isn't found.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(URL_DIARIOS, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []  # list of (label_text, href, full_url)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        href_lower = href.lower()
        if "pdf" in href_lower or "exibe" in href_lower or "diario" in href_lower:
            full_url = requests.compat.urljoin(URL_BASE, href)
            links.append((a.get_text(strip=True), href, full_url))

    if not links:
        return None

    # If DATA_OVERRIDE, look for the specific date in the URL or link text
    if _DATA_OVERRIDE:
        data_str_compact = _DATA_TARGET.strftime("%Y%m%d")   # 20260427
        data_str_slash   = _DATA_TARGET.strftime("%d/%m/%Y")  # 27/04/2026
        data_str_slash2  = _DATA_TARGET.strftime("%d/%m/%y")  # 27/04/26

        for label, href, full_url in links:
            if (data_str_compact in href
                    or data_str_slash in label
                    or data_str_slash2 in label):
                print(f"   ✅ PDF encontrado para {_DATA_OVERRIDE}: {full_url}")
                return full_url

        print(f"   ⚠️  Nenhum link para {_DATA_OVERRIDE}. Usando o mais recente.")

    return links[0][2]


def _analisar_com_gemini(texto: str) -> list[dict]:
    """
    Send PDF text to Gemini and parse the returned JSON list of terrenos.
    Returns empty list on failure.
    """
    if gemini_model is None:
        print("⚠️  GOOGLE_API_KEY não configurado. Pulando análise por IA.")
        return []

    # Limit to first 300k chars to stay within token limits
    texto_limitado = texto[:300000]

    try:
        response = gemini_model.generate_content(
            f"{PROMPT_IA}\n\n--- TEXTO DO DIÁRIO OFICIAL ---\n{texto_limitado}"
        )
        raw = response.text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError as exc:
        print(f"⚠️  Resposta da IA não é JSON válido: {exc}")
        try:
            print(f"   Resposta bruta (primeiros 500 chars): {response.text[:500]}")
        except Exception:
            pass
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
            if kmz_path and not validar_kmz(kmz_path):
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
    data_str = _DATA_TARGET.strftime("%d/%m/%Y")
    print(f"🔍 Monitorando Diário Oficial de Goiânia — {data_str}")
    if FORCE_REPROCESS:
        print("   ⚡ FORCE_REPROCESS=1: ignorando dedup")

    try:
        # ── Find PDF ──────────────────────────────────────────────────────────
        link_pdf = _buscar_link_pdf()
        if not link_pdf:
            msg = f"❌ Nenhum PDF encontrado na página de {ANO_ATUAL}."
            print(msg)
            send_message(msg)
            return

        # ── Dedup check ───────────────────────────────────────────────────────
        if not FORCE_REPROCESS:
            ultimo = _ler_ultimo_diario()
            if link_pdf == ultimo:
                print(f"✅ Diário já processado: {link_pdf}")
                print("   (defina FORCE_REPROCESS=1 para reprocessar)")
                return

        print(f"📥 Baixando: {link_pdf}")
        headers  = {"User-Agent": "Mozilla/5.0"}
        pdf_resp = requests.get(link_pdf, headers=headers, timeout=60)
        pdf_resp.raise_for_status()
        print(f"   📄 PDF baixado: {len(pdf_resp.content):,} bytes")

        # ── Extract text ──────────────────────────────────────────────────────
        texto = _extrair_texto_pdf(pdf_resp.content)
        if not texto.strip():
            msg = (
                f"⚠️ PDF encontrado mas sem texto extraível.\n"
                f"🔗 <a href='{link_pdf}'>Ver PDF</a>"
            )
            send_message(msg)
            return

        print(f"   📝 Texto extraído: {len(texto):,} caracteres")

        # Quick sanity count for diagnostics
        cert_count = texto.lower().count("certidão")
        print(f"   🔎 Ocorrências de 'certidão' no texto: {cert_count}")

        # ── AI analysis ───────────────────────────────────────────────────────
        print("🤖 Analisando com Gemini...")
        terrenos = _analisar_com_gemini(texto)
        print(f"   IA retornou: {len(terrenos)} registro(s)")

        if not terrenos:
            msg = (
                f"🏛️ <b>Diário Oficial ({data_str})</b>\n"
                f"Nenhuma certidão imobiliária encontrada.\n"
                f"🔎 Ocorrências de 'certidão' no PDF: {cert_count}\n"
                f"🔗 <a href='{link_pdf}'>Ver PDF</a>"
            )
            send_message(msg)
            _salvar_ultimo_diario(link_pdf)
            return

        # ── Process each terreno ──────────────────────────────────────────────
        send_message(
            f"🏛️ <b>Diário Oficial ({data_str})</b>\n"
            f"Encontrado(s) <b>{len(terrenos)}</b> certidão(ões) imobiliária(s).\n"
            f"🔗 <a href='{link_pdf}'>Ver PDF</a>\n"
            f"Gerando KMZs e notificando..."
        )

        for terreno in terrenos:
            processar_terreno(terreno, link_pdf)

        # ── Mark as processed ─────────────────────────────────────────────────
        _salvar_ultimo_diario(link_pdf)
        print(f"\n✅ Pipeline concluído. {len(terrenos)} terreno(s) processado(s).")

    except Exception as exc:
        erro_detalhado = traceback.format_exc()
        print(f"❌ Erro crítico:\n{erro_detalhado}")
        send_message(f"❌ Erro crítico no script:\n{exc}")


if __name__ == "__main__":
    # FIX: attempt Telegram notification even when env vars are missing
    missing = [v for v in ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_API_KEY"]
               if not os.environ.get(v)]

    if missing:
        msg = (
            f"❌ Variáveis de ambiente faltando: {missing}\n"
            f"Configure em .env ou nos Secrets do GitHub Actions."
        )
        print(msg)
        # If only GOOGLE_API_KEY missing, Telegram still works — warn via bot
        if "TELEGRAM_TOKEN" not in missing and "TELEGRAM_CHAT_ID" not in missing:
            send_message(f"⚠️ Script iniciado sem GOOGLE_API_KEY.\n{msg}")
        raise SystemExit(1)

    main()
