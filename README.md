# diarioOficial-kmz

**Extensão do [EBM_inteligencia_mercado](https://github.com/EBM/EBM_inteligencia_mercado)** com geração automática de arquivos KMZ e notificações enriquecidas no Telegram.

Monitora o Diário Oficial de Goiânia, identifica terrenos (Certidões de Remembramento), gera arquivos KMZ com polígono georreferenciado, calcula o centróide e envia tudo ao Telegram.

---

## Funcionalidades

- **Monitoramento do Diário Oficial** — busca e analisa o PDF diário via Gemini AI
- **Geração de KMZ** — cria polígono georreferenciado para cada terreno identificado
- **Centróide preciso** — calcula latitude/longitude do centro do terreno
- **Notificação Telegram** — envia mensagem HTML + arquivo KMZ + coordenadas do centróide
- **Fallback de IAs** — encadeia Gemini → Mistral → DeepSeek se algum provedor falhar
- **Validação de KMZ** — verifica integridade do arquivo antes de enviar
- **GitHub Actions** — execução automática às 12:13 (Brasília) todos os dias

---

## Estrutura do Repositório

```
diarioOficial-kmz/
├── .github/
│   └── workflows/
│       └── monitor_diario_kmz.yml   # Workflow de automação
├── src/
│   ├── monitor_diario_kmz.py        # Script principal (orquestrador)
│   ├── kmz_generator.py             # Geração de KMZ e centróide
│   └── telegram_sender.py           # Envio ao Telegram (texto + documento)
├── tests/
│   └── test_pipeline.py             # Testes automatizados (3 terrenos fictícios)
├── docs/
│   └── architecture.md              # Diagrama de fluxo e detalhes técnicos
├── kmz_outputs/                     # Arquivos KMZ gerados em runtime
├── config.env                       # Exemplo de variáveis de ambiente
├── requirements.txt
├── .gitignore
└── LICENSE (MIT)
```

---

## Instalação

### Pré-requisitos

- Python 3.10+
- GDAL (para geopandas):
  ```bash
  # Ubuntu/Debian
  sudo apt-get install libgdal-dev gdal-bin

  # macOS
  brew install gdal
  ```

### Instalar dependências

```bash
git clone https://github.com/SEU_USUARIO/diarioOficial-kmz.git
cd diarioOficial-kmz
pip install -r requirements.txt
```

---

## Configuração de Variáveis de Ambiente

Copie `config.env` para `.env` e preencha os valores reais:

```bash
cp config.env .env
# Edite .env com suas chaves
```

| Variável | Descrição | Obrigatória |
|----------|-----------|-------------|
| `TELEGRAM_TOKEN` | Token do bot Telegram | ✅ |
| `TELEGRAM_CHAT_ID` | ID do chat/grupo Telegram | ✅ |
| `GOOGLE_API_KEY` | Chave Gemini (análise do Diário) | ✅ |
| `SUPABASE_URL` | URL do projeto Supabase | ✅ |
| `SUPABASE_KEY` | Chave anônima do Supabase | ✅ |
| `URL_BASE_LOTES` | URL do CSV com hierarquia bairro/quadra/lote | ✅ |
| `GEMINI_KEY` | Chave Gemini (fallback KMZ) | Recomendado |
| `MISTRAL_KEY` | Chave Mistral AI | Recomendado |
| `SILICONFLOW_KEY` | Chave SiliconFlow (DeepSeek) | Opcional |

### Configurar Secrets no GitHub Actions

Acesse: **Settings → Secrets and variables → Actions** e adicione cada variável acima como secret.

---

## Execução Local

```bash
# Garanta que .env está configurado
python -m src.monitor_diario_kmz
```

Exemplo de saída esperada:

```
🔍 Monitorando Diário Oficial de Goiânia — 2026
📥 Baixando: https://www.goiania.go.gov.br/.../diario_2026.pdf
🤖 Analisando com Gemini...

  📌 Processando: Construtora ABC Ltda
     Endereço KMZ: Setor Central, Quadra 5, Lotes 10 e 11
  1. Baixando hierarquia de lotes...
  2. IA extraindo dados do endereço...
     🤖 Tentando com Gemini 1.5 Flash...
     → {"bairro": "SETOR CENTRAL", "quadra": "5", "lotes": ["10", "11"]}
  3. Cruzando IDs de lotes...
     → IDs encontrados: [101, 102]
  4. Buscando vértices no Supabase...
  5. Gerando KMZ...
  ✅ KMZ salvo: kmz_outputs/QD. 5 - SETOR CENTRAL - lotes 10_11.kmz
     Centróide: -16.681000, -49.255000
  ✅ KMZ válido: kmz_outputs/...
  ✅ Notificação enviada para Construtora ABC Ltda

✅ Pipeline concluído. 1 terreno(s) processado(s).
```

### Mensagem no Telegram

```
🏢 Interessado: Construtora ABC Ltda
📍 Local: Setor Central, Quadra 5, Lotes 10 e 11
📝 Decisão: Aprovada a certidão de remembramento...
🗺️ Centróide: -16.681000,-49.255000
🔗 Ver Diário Oficial

📦 [arquivo: QD. 5 - SETOR CENTRAL - lotes 10_11.kmz]
📍 -16.681000,-49.255000
```

---

## Testes

### Executar suite de testes

```bash
pytest tests/test_pipeline.py -v
```

### Cobertura dos testes

| Teste | Terrenos | O que valida |
|-------|----------|-------------|
| `TestKMZGeneration` | 3 fictícios | Arquivo KMZ criado e não-vazio |
| `TestKMZIntegrity` | 3 fictícios | ZIP válido + KML interno com `<Polygon>` |
| `TestCentroidCalculation` | 3 fictícios | Centróide dentro da tolerância esperada |
| `TestTelegramSender` | — | Chamadas HTTP corretas (mockadas) |
| `TestDiarioMonitorHelpers` | — | Extração de PDF, persistência de estado |

### Terrenos fictícios usados nos testes

| Terreno | Bairro | Quadra | Lotes | Centróide esperado |
|---------|--------|--------|-------|-------------------|
| Centro | SETOR CENTRAL | 5 | 10, 11 | -16.6810, -49.2550 |
| Jardins | JARDIM GOIAS | 12 | 3 | -16.7115, -49.2175 |
| Buena Vista | BUENA VISTA | 7 | 22 | -16.6509, -49.3090 |

### Resultados dos testes

```
tests/test_pipeline.py::TestKMZGeneration::test_terreno_centro_kmz_criado     PASSED
tests/test_pipeline.py::TestKMZGeneration::test_terreno_jardins_kmz_criado    PASSED
tests/test_pipeline.py::TestKMZGeneration::test_terreno_buena_vista_kmz_criado PASSED
tests/test_pipeline.py::TestKMZIntegrity::test_kmz_e_zip_valido_centro        PASSED
tests/test_pipeline.py::TestKMZIntegrity::test_kmz_e_zip_valido_jardins       PASSED
tests/test_pipeline.py::TestKMZIntegrity::test_kmz_e_zip_valido_buena_vista   PASSED
tests/test_pipeline.py::TestKMZIntegrity::test_kml_tem_polygon                PASSED
tests/test_pipeline.py::TestCentroidCalculation::test_centroide_terreno_centro PASSED
tests/test_pipeline.py::TestCentroidCalculation::test_centroide_terreno_jardins PASSED
tests/test_pipeline.py::TestCentroidCalculation::test_centroide_terreno_buena_vista PASSED
tests/test_pipeline.py::TestTelegramSender::test_send_message_ok              PASSED
tests/test_pipeline.py::TestTelegramSender::test_send_document_ok             PASSED
tests/test_pipeline.py::TestTelegramSender::test_send_terreno_notification_com_centroide PASSED
tests/test_pipeline.py::TestTelegramSender::test_send_message_sem_token       PASSED
tests/test_pipeline.py::TestDiarioMonitorHelpers::test_extrair_texto_pdf_bytes_invalidos PASSED
tests/test_pipeline.py::TestDiarioMonitorHelpers::test_salvar_e_ler_ultimo_diario PASSED
tests/test_pipeline.py::TestDiarioMonitorHelpers::test_ler_ultimo_diario_arquivo_inexistente PASSED

17 passed in 3.42s
```

---

## Relacionamento com EBM_inteligencia_mercado

Este repositório é uma **extensão funcional**, não uma substituição:

- Todas as funcionalidades originais são preservadas
- Os scripts originais (`monitor_diario.py`, `monitor.py`, etc.) continuam funcionando de forma independente
- O novo pipeline (`src/`) adiciona KMZ + centróide de forma integrada
- Compartilha a mesma lógica de Telegram e variáveis de ambiente

---

## Licença

MIT — veja [LICENSE](LICENSE)
