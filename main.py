from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
import csv
from datetime import date, datetime
import html
import io
import json
from math import ceil
import mimetypes
import os
import re
import secrets
import threading
from typing import Any, Literal
import unicodedata
from urllib.parse import urlencode
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

try:
    from db import init_db, get_session, engine
    from models import Report, Template
except ModuleNotFoundError:
    from .db import init_db, get_session, engine
    from .models import Report, Template

try:
    from weasyprint import HTML
except Exception:
    HTML = None


DEFAULT_TEMPLATE = """<section>
  <h1>Relatorio para {{ client }}</h1>
  <p><strong>Data:</strong> {{ date }}</p>
  <h2>Itens</h2>
  <ul>
  {% for item in items %}
    <li>{{ item.name }}: {{ item.qty }}</li>
  {% endfor %}
  </ul>
</section>
"""
PDF_CSS = """
@page {
  size: A4;
  margin: 3cm 2cm 2cm 3cm;
  @top-center {
    content: element(pdf-header);
  }
  @top-right {
    content: counter(page);
    font-size: 10pt;
    font-family: "Arial", sans-serif;
  }
}

@page :first {
  @top-right {
    content: "";
  }
}

body.pdf-mode {
  background: #fff;
}

body.pdf-mode::before,
body.pdf-mode::after {
  display: none;
  content: none;
}

body.pdf-mode .shell {
  max-width: none;
  padding: 0;
}

body.pdf-mode .markdown-preview {
  border: 0;
  box-shadow: none;
  background: #fff;
  margin: 0 auto;
  max-width: none;
  padding: 0;
}

body.pdf-mode .pdf-header {
  position: running(pdf-header);
  display: block;
  height: 0;
  margin: 0;
  overflow: visible;
  text-align: center;
}

body.pdf-mode .pdf-header img {
  max-height: 16mm;
  margin-top: 0;
}

body.pdf-mode .markdown-preview .print-cover {
  margin: -30mm -20mm -20mm -30mm;
  min-height: 297mm;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

body.pdf-mode .markdown-preview .print-cover h1 {
  margin: 0;
  font-size: 18pt;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
  font-family: "Arial", sans-serif;
  text-align: center;
  line-height: 1.08;
  text-indent: 0;
  width: 100%;
}

body.pdf-mode .markdown-preview .print-cover img {
  margin-top: 0;
}

@media print {
  body.pdf-mode * {
    visibility: visible;
  }

  body.pdf-mode .print-target {
    position: static;
    inset: auto;
  }

  body.pdf-mode .markdown-preview {
    padding: 0;
  }
}
"""

def strip_css_imports(css_text: str) -> str:
    return re.sub(r"@import\\s+url\\([^;]+\\);\\s*", "", css_text)

DEFAULT_DATA = """{
  "client": "Acme Corp",
  "date": "2025-01-01",
  "items": [
    {"name": "Widget A", "qty": 2},
    {"name": "Widget B", "qty": 5}
  ]
}
"""
TUTORIAL_PLACEHOLDER = "{{ client }}"
TUTORIAL_JSON_INLINE = '{ "client": "Ana", "date": "2025-01-01", "items": [] }'
TUTORIAL_TEMPLATE_EXAMPLE = "<h1>Relatorio para {{ client }}</h1>"
TUTORIAL_JSON_EXAMPLE = """{
  "client": "Ana",
  "date": "2025-01-01",
  "items": [
    {"name": "Item A", "qty": 1}
  ]
}"""
TUTORIAL_SCHEMA_HINT = (
    "Estrutura livre: use as mesmas chaves que aparecem no template."
)
CSV_EXAMPLE_TEXT = """date;sku;product;qty;revenue
2025-04-01;A-100;Kit Premium A;10;5200
2025-04-02;B-220;Combo B;18;3240
2025-04-03;C-310;Produto C;8;1680
"""
CSV_EXAMPLE_TEMPLATE = """<section>
  <h1>Relatorio CSV - {{ file_name }}</h1>
  <p><strong>Delimiter:</strong> {{ delimiter }}</p>
  <p><strong>Linhas:</strong> {{ rows_returned }} / {{ total_rows }}</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
        {% for h in headers %}
          <th>{{ h }}</th>
        {% endfor %}
        </tr>
      </thead>
      <tbody>
      {% for row in rows %}
        <tr>
        {% for h in headers %}
          <td>{{ row[h] }}</td>
        {% endfor %}
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>
"""
HOFTALON_TITLE = "RELATÓRIO TÉCNICO DE INVENTÁRIO"
HOFTALON_IDENT_LINES = [
    "Cidade:",
    "Unidade:",
    "Período:",
    "Data-base:",
    "Responsável técnico:",
]
HOFTALON_SECTION_HEADERS = [
    "1. OBJETIVO",
    "2. ESCOPO",
    "3. METODOLOGIA",
    "4. RESULTADOS",
    "5. PLANO DE AÇÃO",
    "6. ACHADOS / OBSERVAÇÕES",
]
HOFTALON_SUBSECTION_HEADERS = ["4.1", "4.2"]
HOFTALON_REQUIRED_FIELDS = [
    "cidade",
    "unidade",
    "periodo",
    "data_base",
    "responsavel_tecnico",
    "objetivo",
    "escopo",
    "metodologia",
    "achados",
]
HOFTALON_REQUIRED_TABLE_KEYS = ["resultados_1", "resultados_2", "atividades"]
HOFTALON_CUSTOM_PAGE_LAYOUTS = {"cover", "page", "toc"}
MAX_CUSTOM_PAGES = 8
MAX_CUSTOM_PAGE_CHARS = 8000
MAX_LOGO_BYTES = 2_000_000
HOFTALON_FORBIDDEN_TERMS = [
    "json",
    "sql",
    "id",
    "template_id",
    "endpoint",
    "api",
    "fastapi",
    "jinja",
    "tables_md",
    "tables_html",
    "tables_meta",
    "database",
    "commit",
]
HOFTALON_ACTIVIDADES_COLUMNS = [
    "atividade",
    "responsavel",
    "prazo_dias",
    "prioridade",
    "observacao",
]
HOFTALON_ACTIVIDADES_SYNONYMS = {
    "atividade": {"acao", "atividadeplanejada", "task"},
    "responsavel": {"responsaveltecnico", "responsaveltécnico", "owner"},
    "prazo_dias": {"prazo", "dias", "deadline", "prazoemdias"},
    "prioridade": {"priority"},
    "observacao": {"observacoes", "obs", "comentario", "comentarios"},
}
HOFTALON_BASE_TEMPLATE = """{% if logo_url %}
<div class="pdf-header">
  <img src="{{ logo_url }}" alt="Logo">
</div>
{% endif %}

{% if show_base_cover %}
<section class="print-cover">
  <h1>RELATÓRIO TÉCNICO DE INVENTÁRIO</h1>
</section>
<div class="page-break"></div>
{% endif %}

{% if custom_pages_html %}
{{ custom_pages_html }}
<div class="page-break"></div>
{% endif %}

{% if show_base_toc %}
<section class="print-toc">
  <h2>SUMÁRIO</h2>
  <ul>
  {% for item in sumario %}
    <li><span>{{ item }}</span></li>
  {% endfor %}
  </ul>
</section>
<div class="page-break"></div>
{% endif %}

<section class="report-meta">
  <p><strong>Cidade:</strong> {{ cidade }}</p>
  <p><strong>Unidade:</strong> {{ unidade }}</p>
  <p><strong>Período:</strong> {{ periodo }}</p>
  <p><strong>Data-base:</strong> {{ data_base }}</p>
  <p><strong>Responsável técnico:</strong> {{ responsavel_tecnico }}</p>
</section>

<h2>1. OBJETIVO</h2>
<p>{{ objetivo }}</p>

<h2>2. ESCOPO</h2>
<p>{{ escopo }}</p>

<h2>3. METODOLOGIA</h2>
<p>{{ metodologia }}</p>

<h2>4. RESULTADOS</h2>

<h3>4.1 {{ resultados_4_1_titulo }}{{ resultados_4_1_sufixo }}</h3>
{% if resultados_4_1_nota %}
<p class="note">{{ resultados_4_1_nota }}</p>
{% endif %}
{{ tables_html["resultados_1"] }}

<h3>4.2 {{ resultados_4_2_titulo }}{{ resultados_4_2_sufixo }}</h3>
{% if resultados_4_2_nota %}
<p class="note">{{ resultados_4_2_nota }}</p>
{% endif %}
{{ tables_html["resultados_2"] }}

<h2>5. PLANO DE AÇÃO</h2>
{% if plano_intro %}
<p>{{ plano_intro }}</p>
{% endif %}
{{ tables_html["atividades"] }}

<h2>6. ACHADOS / OBSERVAÇÕES</h2>
<ul>
{% for item in achados %}
  <li>{{ item }}</li>
{% endfor %}
</ul>
"""
FLOW_TEMPLATE_EXAMPLE = HOFTALON_BASE_TEMPLATE
FLOW_DATA_EXAMPLE = """{
  "logo_url": "__LOGO_URL__",
  "cidade": "Sao Paulo",
  "unidade": "Unidade Matriz",
  "periodo": "Q2 2025",
  "data_base": "2025-06-30",
  "responsavel_tecnico": "Mariana Alves",
  "objetivo": "Apresentar o inventario tecnico e consolidar as evidencias do periodo.",
  "escopo": "Abrange ativos patrimoniais cadastrados nas unidades administrativas e operacionais.",
  "metodologia": "Coleta documental, validacao fisica e cruzamento com base patrimonial.",
  "achados": [
    "Foram identificadas divergencias pontuais de placa patrimonial.",
    "Acuracia geral acima de 95% nos ativos verificados.",
    "Necessidade de regularizacao em itens sem numero de serie."
  ],
  "custom_pages": [
    {
      "layout": "page",
      "content": "<h2>Apresentacao</h2><p>Relatorio elaborado para {{ unidade }} com base no periodo informado.</p>"
    }
  ]
}
"""
FLOW_TABLE_CSV_EXAMPLE = """categoria,status,quantidade
Moveis,Conforme,120
Informatica,Divergente,8
Equipamentos,Conforme,45
"""
FLOW_TABLE_CSV_EXAMPLE_2 = """unidade,verificados,pendentes
Matriz,300,12
Filial A,180,6
Filial B,210,9
"""
FLOW_TABLE_CSV_EXAMPLE_ACTIVIDADES = """atividade,responsavel,prazo_dias,prioridade,observacao
Regularizar placas pendentes,Equipe Patrimonio,15,Alta,Executar auditoria local
Atualizar base patrimonial,Backoffice,10,Media,Conferencia com registros legados
Validar ativos sem numero de serie,Gestao,20,Alta,Planejar nova coleta
"""
MAX_TEMPLATE_CHARS = 1000000
MAX_TEMPLATE_KEY_CHARS = 80
MAX_DATA_CHARS = 200000
MAX_OUTPUT_CHARS = 1000000
MAX_RENDER_SECONDS = 2.0
MAX_CSV_BYTES = 2_000_000
MAX_CSV_ROWS = 1000
MAX_CSV_COLUMNS = 50
MAX_CELL_CHARS = 500
DEFAULT_CSV_PREVIEW_ROWS = 200
MAX_LLM_TABLES = 5
DEFAULT_LOGO_PATH = str(
    Path(__file__).resolve().parent / "assets" / "logo.png"
)
PRIVATE_LOGO_PATH = os.getenv("PRIVATE_LOGO_PATH", DEFAULT_LOGO_PATH)
PRIVATE_LOGO_TOKEN = os.getenv("PRIVATE_LOGO_TOKEN", "")
PRIVATE_LOGO_MEDIA_TYPE = os.getenv("PRIVATE_LOGO_MEDIA_TYPE", "")
PRIVATE_LOGO_CACHE_SECONDS = 3600
PRIVATE_LOGO_REQUIRE_TOKEN = os.getenv(
    "PRIVATE_LOGO_REQUIRE_TOKEN", "false"
).strip().lower() not in {"0", "false", "no", "off"}
PRIVATE_LOGO_URL_EXAMPLE = (
    "/private/logo?token=troque_este_token"
    if PRIVATE_LOGO_REQUIRE_TOKEN
    else "/private/logo"
)
FLOW_DATA_EXAMPLE = FLOW_DATA_EXAMPLE.replace(
    "__LOGO_URL__", PRIVATE_LOGO_URL_EXAMPLE
)
LLM_DEFAULT_MODEL = os.getenv("LLM_MODEL", "granite4:small-h")
LLM_DEFAULT_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.0.12:11434/")
try:
    LLM_DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
except ValueError:
    LLM_DEFAULT_TEMPERATURE = 0.0
DEFAULT_PER_PAGE = 10
MAX_PER_PAGE = 50
BASE_CSS = """
@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Space+Grotesk:wght@400;600;700&family=Source+Serif+4:wght@400;600&display=swap");

:root {
  --bg-1: #fdf7ee;
  --bg-2: #f0f4f8;
  --ink: #1b1b1f;
  --muted: #5f6b7a;
  --card: #ffffff;
  --line: #e6e1d8;
  --accent: #0f766e;
  --accent-2: #d97706;
  --accent-3: #1f3a5f;
  --shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  color: var(--ink);
  background:
    radial-gradient(circle at 10% 10%, rgba(15, 118, 110, 0.18), transparent 55%),
    radial-gradient(circle at 90% 0%, rgba(217, 119, 6, 0.18), transparent 45%),
    linear-gradient(135deg, var(--bg-1), var(--bg-2));
  font-family: "Source Serif 4", "Georgia", serif;
  line-height: 1.5;
}

body::before,
body::after {
  content: "";
  position: fixed;
  width: 320px;
  height: 320px;
  border-radius: 40%;
  opacity: 0.35;
  pointer-events: none;
  z-index: 0;
}

body::before {
  top: -120px;
  right: -120px;
  background: radial-gradient(circle, rgba(15, 118, 110, 0.35), transparent 70%);
}

body::after {
  bottom: -160px;
  left: -160px;
  background: radial-gradient(circle, rgba(31, 58, 95, 0.25), transparent 70%);
}

.shell {
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
  padding: 32px 20px 60px;
  z-index: 1;
}

nav.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

nav.tabs a.tab {
  text-decoration: none;
  color: var(--ink);
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.6);
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  font-size: 0.9rem;
  letter-spacing: 0.3px;
}

nav.tabs a.tab.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.hero {
  display: flex;
  gap: 24px;
  align-items: flex-start;
  justify-content: flex-start;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 2px;
  color: var(--accent-2);
  font-weight: 700;
  font-size: 0.75rem;
  margin: 0 0 8px 0;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
}

h1 {
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 700;
  font-size: clamp(2rem, 3vw, 2.6rem);
  margin: 0 0 8px 0;
}

h2 {
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
}

.lead {
  margin: 0;
  color: var(--muted);
  font-size: 1rem;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.primary-column {
  display: grid;
  gap: 20px;
  min-width: 0;
}

.primary-column .output-card {
  margin-top: 0;
}

.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 20px;
  box-shadow: var(--shadow);
  animation: rise 0.6s ease both;
}

.card h2 {
  margin-top: 0;
}

.stack {
  display: grid;
  gap: 12px;
}

.field label {
  display: block;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  margin-bottom: 6px;
}

input[type="text"],
input[type="number"],
input[type="file"],
select,
textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  font-family: "IBM Plex Mono", "Courier New", monospace;
  font-size: 0.95rem;
  background: #fff;
}

input[type="file"] {
  padding: 8px;
}

input[type="file"]::file-selector-button {
  border: 1px solid var(--line);
  background: #f6f2ea;
  border-radius: 8px;
  padding: 6px 10px;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  margin-right: 8px;
  cursor: pointer;
}

input[type="text"]:focus,
input[type="number"]:focus,
select:focus,
textarea:focus {
  outline: 3px solid rgba(15, 118, 110, 0.2);
  border-color: var(--accent);
}

textarea {
  min-height: 170px;
}

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.filters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  align-items: end;
}

.summary {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 0.92rem;
}

.btn,
a.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 10px 16px;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.btn:hover,
a.btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.12);
}

.btn:focus-visible,
a.btn:focus-visible {
  outline: 3px solid rgba(15, 118, 110, 0.35);
  outline-offset: 2px;
}

.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.btn.secondary {
  background: #fff;
  border-color: var(--accent);
  color: var(--accent);
}

.btn.ghost {
  background: #fff;
  border-color: var(--line);
  color: var(--ink);
}

.btn.danger {
  background: #fff5f5;
  border-color: #f2b8b5;
  color: #9f2d2d;
}

.btn.disabled {
  opacity: 0.55;
  pointer-events: none;
}

.notice,
.error {
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 10px;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-size: 0.95rem;
}

.notice {
  background: #edf7f0;
  border: 1px solid #bfe3c9;
  color: #1a5b2e;
}

.error {
  background: #fff1f1;
  border: 1px solid #f3b5b5;
  color: #8b1d1d;
}

.code-block {
  background: #0f172a;
  color: #e2e8f0;
  padding: 14px;
  border-radius: 12px;
  white-space: pre-wrap;
  font-family: "IBM Plex Mono", "Courier New", monospace;
  font-size: 0.92rem;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
}

.badge.active {
  background: #e0f2f1;
  color: #0f766e;
}

.badge.inactive {
  background: #f3f4f6;
  color: #4b5563;
}

.badge.accent {
  background: #fff4e5;
  color: #b45309;
}

.steps {
  margin: 0 0 12px 18px;
  color: var(--muted);
}

.hint {
  color: var(--muted);
  margin: 0 0 16px 0;
}

.output-card {
  margin-top: 22px;
}

.output-card .card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.markdown-preview {
  padding: 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--line);
  font-family: "Source Serif 4", "Georgia", serif;
  line-height: 1.6;
  word-break: break-word;
}

.markdown-preview .pdf-header {
  display: none;
}

.markdown-preview.preview-empty {
  color: var(--muted);
  font-style: italic;
  min-height: 120px;
}

.markdown-preview h1,
.markdown-preview h2,
.markdown-preview h3,
.markdown-preview h4,
.markdown-preview h5,
.markdown-preview h6 {
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  margin-top: 18px;
  margin-bottom: 8px;
}

.markdown-preview h1 {
  font-size: 1.6rem;
}

.markdown-preview h2 {
  font-size: 1.3rem;
}

.markdown-preview h4 {
  font-size: 1.1rem;
}

.markdown-preview h5 {
  font-size: 1rem;
}

.markdown-preview h6 {
  font-size: 0.95rem;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}

.markdown-preview p {
  margin: 0 0 12px 0;
}

.markdown-preview p:last-child {
  margin-bottom: 0;
}

.markdown-preview strong {
  font-weight: 700;
}

.markdown-preview em {
  font-style: italic;
}

.markdown-preview mark {
  background: rgba(217, 119, 6, 0.2);
  padding: 0 4px;
  border-radius: 4px;
}

.markdown-preview sup,
.markdown-preview sub {
  font-size: 0.75em;
}

.markdown-preview abbr[title] {
  text-decoration: underline dotted;
  text-underline-offset: 3px;
  cursor: help;
}

.markdown-preview ul,
.markdown-preview ol {
  margin: 0 0 12px 20px;
  padding-left: 18px;
}

.markdown-preview li {
  margin: 0 0 6px 0;
}

.markdown-preview li > p {
  margin: 0;
}

.markdown-preview a {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 3px;
  font-weight: 600;
}

.markdown-preview a:hover {
  color: var(--accent-3);
}

.markdown-preview pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 10px;
  overflow: auto;
  tab-size: 2;
}

.markdown-preview code {
  font-family: "IBM Plex Mono", "Courier New", monospace;
  background: #f6f2ea;
  padding: 2px 4px;
  border-radius: 4px;
  word-break: break-word;
}

.markdown-preview pre code {
  background: transparent;
  padding: 0;
}

.markdown-preview blockquote {
  border-left: 3px solid var(--accent);
  padding-left: 12px;
  color: var(--muted);
  margin: 12px 0;
  background: rgba(15, 118, 110, 0.05);
  border-radius: 8px;
  padding-top: 6px;
  padding-bottom: 6px;
}

.markdown-preview hr {
  border: 0;
  height: 1px;
  background: var(--line);
  margin: 18px 0;
}

.markdown-preview img {
  max-width: 100%;
  margin: 8px 0;
}

.markdown-preview figure {
  margin: 0 0 12px 0;
}

.markdown-preview figcaption {
  color: var(--muted);
  font-size: 0.9rem;
  margin-top: 6px;
}

.markdown-preview .table-wrap {
  margin: 12px 0;
  border-radius: 12px;
  border: 1px solid var(--line);
  overflow-x: auto;
  background: #fff;
  max-width: 100%;
}

.markdown-preview table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  margin: 0;
  font-size: 0.95rem;
  table-layout: auto;
}

.markdown-preview th,
.markdown-preview td {
  border: 1px solid var(--line);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: normal;
  word-break: normal;
  white-space: nowrap;
}

.markdown-preview th {
  background: #f8f4ed;
  font-weight: 600;
}

.markdown-preview tbody tr:nth-child(even) {
  background: #fbf8f2;
}

.markdown-preview table caption {
  caption-side: bottom;
  padding: 8px;
  color: var(--muted);
  font-size: 0.85rem;
}

.markdown-preview dl {
  margin: 0 0 12px 0;
}

.markdown-preview dt {
  font-weight: 700;
  margin-top: 8px;
}

.markdown-preview dd {
  margin: 0 0 8px 16px;
  color: var(--muted);
}

.markdown-preview del {
  color: var(--muted);
}

.markdown-preview kbd {
  font-family: "IBM Plex Mono", "Courier New", monospace;
  background: #f6f2ea;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 0.9em;
}

.markdown-preview input[type="checkbox"] {
  margin-right: 6px;
}

.markdown-preview .task-list {
  list-style: none;
  padding-left: 0;
  margin-left: 0;
}

.markdown-preview .task-list-item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 0;
}

.markdown-preview .footnotes {
  font-size: 0.9rem;
  color: var(--muted);
}

.markdown-preview .footnotes-sep {
  border: 0;
  height: 1px;
  background: var(--line);
  margin: 16px 0;
}

.markdown-preview .footnote-ref a,
.markdown-preview .footnote-backref {
  color: var(--accent-3);
  text-decoration: none;
}

.markdown-preview .page-break {
  height: 1px;
  margin: 18px 0;
  background: repeating-linear-gradient(
    90deg,
    rgba(148, 163, 184, 0.6),
    rgba(148, 163, 184, 0.6) 6px,
    transparent 6px,
    transparent 12px
  );
}

.markdown-preview .print-cover {
  min-height: 86vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  text-align: center;
  padding: 22mm 0 18mm;
  gap: 6px;
}

.markdown-preview .print-cover p {
  margin: 0;
}

.markdown-preview .print-cover h1 {
  font-size: 2.1rem;
  letter-spacing: 1px;
  text-transform: uppercase;
  margin: auto 0;
}

.markdown-preview .print-cover img {
  max-width: 180px;
  border: 0;
  box-shadow: none;
}

.markdown-preview .print-toc h2 {
  text-align: center;
  margin-bottom: 16px;
}

.markdown-preview .print-toc ul {
  list-style: none;
  padding-left: 0;
  margin: 0;
}

.markdown-preview .print-toc li {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dotted rgba(148, 163, 184, 0.8);
}

.markdown-preview .print-toc li span {
  text-align: right;
}

.print-header {
  display: none;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  font-family: "Arial", "Times New Roman", "Times", sans-serif;
  font-size: 18px;
}

.print-header img {
  max-height: 38px;
}

.abnt-mode .markdown-preview {
  font-family: "Arial", sans-serif;
  font-size: 12pt;
  line-height: 1.5;
  text-align: left;
  color: #000;
  font-style: normal;
  width: 100%;
  max-width: 210mm;
  min-height: 297mm;
  margin: 0 auto;
  border-radius: 0;
  border-color: #e5e7eb;
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.08);
}

.abnt-mode .markdown-preview * {
  font-family: "Arial", sans-serif;
}

.abnt-mode .markdown-preview h1,
.abnt-mode .markdown-preview h2,
.abnt-mode .markdown-preview h3,
.abnt-mode .markdown-preview h4,
.abnt-mode .markdown-preview h5,
.abnt-mode .markdown-preview h6 {
  text-transform: uppercase;
  letter-spacing: 0;
  text-align: left;
  font-weight: 700;
  font-size: 12pt;
  margin: 16pt 0 12pt 0;
}

.abnt-mode .markdown-preview h1 {
  margin: 0 0 12pt 0;
}

.abnt-mode .markdown-preview h2 {
  margin: 16pt 0 12pt 0;
}

.abnt-mode .markdown-preview h3 {
  text-transform: capitalize;
  margin: 12pt 0 12pt 0;
}

.abnt-mode .markdown-preview h4,
.abnt-mode .markdown-preview h5,
.abnt-mode .markdown-preview h6 {
  text-transform: capitalize;
}

.abnt-mode .markdown-preview p {
  text-indent: 1.25cm;
  margin: 0;
  text-align: justify;
  font-weight: 400;
  text-transform: none;
}

.abnt-mode .markdown-preview li {
  text-align: justify;
  margin: 0;
  text-transform: none;
}

.abnt-mode .markdown-preview p:last-child {
  margin-bottom: 0;
}

.abnt-mode .markdown-preview ul,
.abnt-mode .markdown-preview ol {
  text-align: left;
  margin: 0 0 0 1.25cm;
  padding-left: 0;
}

.abnt-mode .markdown-preview li > p {
  text-indent: 0;
}

.abnt-mode .markdown-preview strong {
  font-weight: 700;
}

.abnt-mode .markdown-preview em {
  font-style: italic;
}

.abnt-mode .markdown-preview .table-wrap {
  border-radius: 0;
  box-shadow: none;
  border: 0;
}

.abnt-mode .markdown-preview table {
  font-size: 10pt;
  line-height: 1;
  border-color: #000;
  width: 100%;
  table-layout: auto;
}

.abnt-mode .markdown-preview th,
.abnt-mode .markdown-preview td {
  padding: 4px 6px;
  border-color: #000;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1;
  text-align: left;
}

.abnt-mode .markdown-preview th.num,
.abnt-mode .markdown-preview td.num {
  text-align: right;
}

.abnt-mode .markdown-preview th {
  background: #fff;
  font-weight: 700;
}

.abnt-mode .markdown-preview table caption {
  caption-side: top;
  text-align: left;
  font-weight: 700;
  padding: 0 0 6pt 0;
  color: #000;
  font-size: 10pt;
}

.abnt-mode .markdown-preview blockquote {
  background: transparent;
  color: #000;
  margin: 0 0 0 4cm;
  padding: 0;
  font-size: 10pt;
  line-height: 1;
  text-align: justify;
  border-left: 0;
}

.abnt-mode .markdown-preview blockquote p {
  text-indent: 0;
  margin: 0;
}

.abnt-mode .markdown-preview code,
.abnt-mode .markdown-preview pre {
  font-family: inherit;
  font-size: 10pt;
  line-height: 1;
}

.abnt-mode .markdown-preview a {
  color: inherit;
  text-decoration: none;
}

.abnt-mode .markdown-preview abbr[title] {
  text-decoration: none;
}

.abnt-mode .markdown-preview .footnotes {
  font-size: 10pt;
  line-height: 1;
}

.abnt-mode .markdown-preview .references li,
.abnt-mode .markdown-preview .referencias li {
  line-height: 1;
  margin-bottom: 12pt;
}

.abnt-mode .markdown-preview .print-cover {
  min-height: 297mm;
  margin: -30mm -20mm -20mm -30mm;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 0;
}

.abnt-mode .markdown-preview .print-cover h1 {
  text-transform: none;
  letter-spacing: 0;
  font-size: 18pt;
  font-weight: 400;
  font-family: "Arial", sans-serif;
  text-align: center;
  line-height: 1.08;
  text-indent: 0;
  width: 100%;
}

.abnt-mode .markdown-preview .print-cover img {
  max-width: 48mm;
  margin: 0;
}

.abnt-mode .markdown-preview .print-toc li {
  border-bottom: 1px dotted rgba(0, 0, 0, 0.6);
}
.raw-output {
  margin-top: 12px;
}

.raw-output summary {
  cursor: pointer;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  color: var(--accent-3);
}

.copy-btn {
  margin-left: auto;
}

.copy-status {
  font-size: 0.85rem;
  color: var(--muted);
  margin-left: 8px;
}

.preview-frame {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 0;
  border-radius: 0;
  background: transparent;
  border: 0;
}

.preview-frame .markdown-preview {
  margin: 0 auto;
}

.pdf-iframe {
  width: 100%;
  max-width: 840px;
  min-height: 1122px;
  height: 70vh;
  border: 0;
  display: none;
  background: #fff;
  border-radius: 0;
  box-shadow: none;
}

.abnt-mode .markdown-preview.pdf-preview {
  position: relative;
  background: #fff;
  border: 0;
  box-shadow: none;
}

.abnt-mode .markdown-preview.pdf-preview .pdf-header {
  display: flex;
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  justify-content: center;
  pointer-events: none;
}

.abnt-mode .markdown-preview.pdf-preview .pdf-header img {
  max-height: 16mm;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  gap: 12px;
}

.page-info {
  color: var(--muted);
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
}

.table-list {
  display: grid;
  gap: 12px;
}

.table-item {
  border: 1px dashed var(--line);
  border-radius: 14px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.7);
}

.table-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.btn.small {
  padding: 6px 10px;
  font-size: 0.85rem;
}

.table-csv {
  min-height: 140px;
}

.flow-results {
  display: grid;
  gap: 16px;
  margin-top: 4px;
}

.flow-results .field {
  display: grid;
  gap: 8px;
}

.flow-results .code-block,
.flow-results .markdown-preview {
  margin: 0;
}

/* sem rolagem interna no preview */

.template-grid {
  display: grid;
  gap: 16px;
}

.template-card {
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 16px;
  background: var(--card);
  box-shadow: var(--shadow);
  animation: rise 0.5s ease both;
  animation-delay: calc(var(--delay) * 0.08s);
}

.template-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.template-title {
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
  margin: 0 0 4px 0;
}

.template-meta {
  color: var(--muted);
  font-size: 0.9rem;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.actions form {
  margin: 0;
}

.link {
  color: var(--accent-3);
  text-decoration: none;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
}

.link:hover {
  text-decoration: underline;
}

.empty {
  padding: 20px;
  border: 1px dashed var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.6);
  color: var(--muted);
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .hero {
    align-items: flex-start;
  }
}

@media print {
  @page {
    size: A4;
    margin: 3cm 2cm 2cm 3cm;
    @top-right {
      content: counter(page);
      font-size: 10pt;
      font-family: "Arial", sans-serif;
    }
  }

  @page :first {
    @top-right {
      content: "";
    }
  }

  body {
    background: #fff;
  }

  body * {
    visibility: hidden;
  }

  .print-target,
  .print-target * {
    visibility: visible;
  }

  .print-target {
    position: absolute;
    inset: 0;
    width: 100%;
    max-width: none;
    padding: 0;
    border: 0;
    box-shadow: none;
    background: #fff;
  }

  .markdown-preview {
    border: 0;
    padding: 1.8cm 0 0 0;
    background: #fff;
  }

  body.abnt-mode .markdown-preview {
    padding: 0;
  }

  .print-header {
    display: flex;
    position: fixed;
    top: 0.6cm;
    left: 0;
    right: 0;
  }

  .markdown-preview h1,
  .markdown-preview h2,
  .markdown-preview h3,
  .markdown-preview h4,
  .markdown-preview h5,
  .markdown-preview h6 {
    break-after: avoid;
    page-break-after: avoid;
  }

  .markdown-preview p,
  .markdown-preview ul,
  .markdown-preview ol {
    widows: 2;
    orphans: 2;
  }

  .markdown-preview table {
    width: 100%;
    min-width: 0;
    break-inside: avoid;
    page-break-inside: avoid;
  }

  .markdown-preview thead {
    display: table-header-group;
  }

  .markdown-preview tfoot {
    display: table-footer-group;
  }

  .markdown-preview tr {
    break-inside: avoid;
    page-break-inside: avoid;
  }

  .markdown-preview .page-break {
    height: 0;
    margin: 0;
    border: 0;
    break-before: page;
    page-break-before: always;
    background: transparent;
  }

  .markdown-preview .print-cover {
    min-height: 92vh;
    padding: 24mm 0 18mm;
  }

  body.abnt-mode .markdown-preview .print-cover {
    padding: 0;
  }

  .markdown-preview .print-cover h1 {
    font-size: 2.4rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  * {
    animation: none !important;
    transition: none !important;
  }
}
"""


class RenderRequest(BaseModel):
    template: str | None = None
    template_id: int | None = None
    template_key: str | None = None
    template_version: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class HtmlPreviewRequest(BaseModel):
    html: str = ""


class HtmlPdfRequest(BaseModel):
    html: str = ""
    title: str | None = None
    auto_print: bool = True


class TableSpec(BaseModel):
    title: str = Field(..., description="Titulo principal (H1), sem tags.")
    description: str = Field(..., description="Descricao curta (1-3 frases).")
    columns: list[str] = Field(
        ..., description="Lista de colunas exatamente como no CSV e na mesma ordem."
    )
    rows: list[dict[str, str]] = Field(
        ...,
        description=(
            "Lista de linhas. Cada linha e um objeto {coluna: valor}. "
            "Todos os valores como string."
        ),
    )


class LLMTableRequest(BaseModel):
    key: str = Field(..., description="Identificador da tabela no template.")
    csv: str = Field(..., description="CSV em texto bruto.")
    delimiter: str | None = Field(
        default=None, description="Delimitador do CSV (ex: ';' ou ',')."
    )
    has_header: bool = Field(default=True, description="CSV possui cabecalho.")
    title: str | None = Field(default=None, description="Titulo opcional para o LLM.")
    description: str | None = Field(
        default=None, description="Descricao opcional para o LLM."
    )


class RenderWithTablesRequest(BaseModel):
    template: str | None = None
    template_id: int | None = None
    template_key: str | None = None
    template_version: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    tables: list[LLMTableRequest] = Field(default_factory=list)
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    append_tables: bool | None = None
    report_style: Literal["default", "hoftalon"] | None = None


jinja_env = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
render_executor = ThreadPoolExecutor(max_workers=4)


def validate_template_text(template_text: str) -> str | None:
    if not template_text.strip():
        return "Template nao pode ser vazio."
    if len(template_text) > MAX_TEMPLATE_CHARS:
        return f"Template muito longo (max {MAX_TEMPLATE_CHARS} caracteres)."
    return None


def normalize_template_key(value: str) -> tuple[str | None, str | None]:
    key = value.strip()
    if not key:
        return None, "Nome do template e obrigatorio."
    if len(key) > MAX_TEMPLATE_KEY_CHARS:
        return (
            None,
            f"Nome do template muito longo (max {MAX_TEMPLATE_KEY_CHARS} caracteres).",
        )
    return key, None


def parse_template_version(value: str) -> tuple[int | None, str | None]:
    if not value or not value.strip():
        return None, "Versao e obrigatoria."
    try:
        version = int(value)
    except ValueError:
        return None, "Versao deve ser um numero inteiro."
    if version < 1:
        return None, "Versao deve ser maior ou igual a 1."
    return version, None


def parse_template_id(value: str) -> tuple[int | None, str | None]:
    if not value or not value.strip():
        return None, None
    try:
        template_id = int(value)
    except ValueError:
        return None, "Template id invalido."
    if template_id < 1:
        return None, "Template id invalido."
    return template_id, None


def resolve_template_for_form(
    session: Session, template_text: str, template_id_value: str
) -> tuple[str | None, Template | None, str | None]:
    template_id, template_error = parse_template_id(template_id_value)
    if template_error:
        return None, None, template_error
    if template_id is not None:
        template_record = session.get(Template, template_id)
        if not template_record:
            return None, None, "Template nao encontrado."
        if not template_record.is_active:
            return None, None, "Template desativado."
        resolved_text = template_record.body
        resolved_error = validate_template_text(resolved_text)
        if resolved_error:
            return None, None, resolved_error
        return resolved_text, template_record, None

    resolved_error = validate_template_text(template_text)
    if resolved_error:
        return None, None, resolved_error
    return template_text, None, None


def resolve_template_for_payload(
    session: Session, payload: RenderRequest
) -> tuple[str | None, Template | None, str | None]:
    if payload.template_id is not None:
        template_record = session.get(Template, payload.template_id)
        if not template_record:
            return None, None, "Template nao encontrado."
        if not template_record.is_active:
            return None, None, "Template desativado."
        resolved_error = validate_template_text(template_record.body)
        if resolved_error:
            return None, None, resolved_error
        return template_record.body, template_record, None

    if payload.template_key and payload.template_version is not None:
        template_record = session.exec(
            select(Template).where(
                Template.key == payload.template_key,
                Template.version == payload.template_version,
            )
        ).first()
        if not template_record:
            return None, None, "Template nao encontrado."
        if not template_record.is_active:
            return None, None, "Template desativado."
        resolved_error = validate_template_text(template_record.body)
        if resolved_error:
            return None, None, resolved_error
        return template_record.body, template_record, None

    if payload.template is None:
        return None, None, "Template ou template_id e obrigatorio."

    resolved_error = validate_template_text(payload.template)
    if resolved_error:
        return None, None, resolved_error
    return payload.template, None, None


def validate_data_text(data_text: str) -> str | None:
    if len(data_text) > MAX_DATA_CHARS:
        return f"JSON muito longo (max {MAX_DATA_CHARS} caracteres)."
    return None


def validate_data_obj(data_obj: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(data_obj, dict):
        return None, "JSON deve ser um objeto."

    try:
        serialized = json.dumps(data_obj, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        return None, f"JSON invalido: {exc}"
    if len(serialized) > MAX_DATA_CHARS:
        return None, f"JSON muito longo (max {MAX_DATA_CHARS} caracteres)."

    return data_obj, None


def render_html(template_text: str, data: dict[str, Any]) -> str:
    template = jinja_env.from_string(template_text)
    return template.render(**data)


def render_html_safe(
    template_text: str, data: dict[str, Any]
) -> tuple[str | None, str | None]:
    try:
        future = render_executor.submit(render_html, template_text, data)
        output_html = future.result(timeout=MAX_RENDER_SECONDS)
    except FutureTimeoutError:
        return None, f"Tempo limite de render (max {MAX_RENDER_SECONDS}s)."
    except Exception as exc:
        return None, f"Erro no template: {exc}"

    if len(output_html) > MAX_OUTPUT_CHARS:
        return None, f"Saida muito longa (max {MAX_OUTPUT_CHARS} caracteres)."

    return output_html, None


def clamp_pagination(page: int | None, per_page: int | None) -> tuple[int, int]:
    page_value = page or 1
    if page_value < 1:
        page_value = 1
    per_page_value = per_page or DEFAULT_PER_PAGE
    if per_page_value < 1:
        per_page_value = DEFAULT_PER_PAGE
    per_page_value = min(per_page_value, MAX_PER_PAGE)
    return page_value, per_page_value


def build_query(params: dict[str, Any]) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    if not clean:
        return ""
    return "?" + urlencode(clean, doseq=True)


def render_pagination(
    base_path: str, page: int, total_pages: int, params: dict[str, Any]
) -> str:
    if total_pages <= 1:
        return ""
    prev_link = f'<span class="btn ghost disabled">Anterior</span>'
    next_link = f'<span class="btn ghost disabled">Proxima</span>'
    if page > 1:
        prev_params = {**params, "page": page - 1}
        prev_link = f'<a class="btn ghost" href="{base_path}{build_query(prev_params)}">Anterior</a>'
    if page < total_pages:
        next_params = {**params, "page": page + 1}
        next_link = f'<a class="btn ghost" href="{base_path}{build_query(next_params)}">Proxima</a>'
    return (
        '<div class="pagination">'
        f"{prev_link}"
        f'<span class="page-info">Pagina {page} de {total_pages}</span>'
        f"{next_link}"
        "</div>"
    )


def render_summary(total: int, page: int, per_page: int) -> str:
    if total == 0:
        return "0 resultados"
    start = (page - 1) * per_page + 1
    end = min(page * per_page, total)
    return f"Mostrando {start}-{end} de {total}"


def parse_date_value(value: str | None) -> tuple[date | None, str | None]:
    if not value or not value.strip():
        return None, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, "Data invalida. Use AAAA-MM-DD."


def clamp_csv_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_CSV_PREVIEW_ROWS
    if limit < 1:
        return 1
    return min(limit, MAX_CSV_ROWS)


def decode_csv_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def normalize_csv_headers(raw_headers: list[str], total_columns: int) -> list[str]:
    headers: list[str] = []
    for idx in range(total_columns):
        value = raw_headers[idx].strip() if idx < len(raw_headers) else ""
        if not value:
            value = f"col_{idx + 1}"
        headers.append(value)
    seen: dict[str, int] = {}
    for idx, name in enumerate(headers):
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count > 1:
            headers[idx] = f"{name}_{count}"
    return headers


def sanitize_csv_cell(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > MAX_CELL_CHARS:
        return cleaned[:MAX_CELL_CHARS] + "..."
    return cleaned


def parse_csv_text(
    text: str, delimiter: str | None, has_header: bool
) -> tuple[list[str], list[dict[str, str]], bool, str, int]:
    sample = text[:4096]
    if delimiter:
        if len(delimiter) != 1:
            raise HTTPException(
                status_code=400,
                detail="Delimitador invalido. Use um unico caractere.",
            )
        dialect = csv.excel
        dialect.delimiter = delimiter
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ","

    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    truncated = False
    total_rows = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        total_rows += 1
        if total_rows > MAX_CSV_ROWS:
            truncated = True
            break
        rows.append(row)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV sem linhas validas.")

    if has_header:
        raw_headers = rows[0]
        data_rows = rows[1:]
        max_columns = max(len(raw_headers), max((len(r) for r in data_rows), default=0))
        if max_columns == 0:
            raise HTTPException(status_code=400, detail="CSV sem colunas validas.")
        if max_columns > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        headers = normalize_csv_headers(raw_headers, max_columns)
    else:
        data_rows = rows
        max_columns = max((len(r) for r in data_rows), default=0)
        if max_columns == 0:
            raise HTTPException(status_code=400, detail="CSV sem colunas validas.")
        if max_columns > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        headers = [f"col_{idx + 1}" for idx in range(max_columns)]

    parsed_rows: list[dict[str, str]] = []
    for row in data_rows:
        cleaned = [sanitize_csv_cell(cell) for cell in row]
        if len(cleaned) < len(headers):
            cleaned.extend([""] * (len(headers) - len(cleaned)))
        elif len(cleaned) > len(headers):
            cleaned = cleaned[: len(headers)]
        parsed_rows.append(dict(zip(headers, cleaned)))

    return headers, parsed_rows, truncated, dialect.delimiter, len(data_rows)


def parse_csv_text_strict(
    text: str, delimiter: str | None, has_header: bool
) -> tuple[list[str], list[dict[str, str]], str]:
    sample = text[:4096]
    if delimiter:
        if len(delimiter) != 1:
            raise HTTPException(
                status_code=400,
                detail="Delimitador invalido. Use um unico caractere.",
            )
        dialect = csv.excel
        dialect.delimiter = delimiter
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ","

    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    for row in reader:
        if not any(cell for cell in row):
            continue
        if len(row) > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        rows.append(row)
        if len(rows) > MAX_CSV_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas linhas (max {MAX_CSV_ROWS}).",
            )

    if not rows:
        raise HTTPException(status_code=400, detail="CSV sem linhas validas.")

    if has_header:
        raw_headers = rows[0]
        data_rows = rows[1:]
        max_columns = max(len(raw_headers), max((len(r) for r in data_rows), default=0))
        if max_columns == 0:
            raise HTTPException(status_code=400, detail="CSV sem colunas validas.")
        if max_columns > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        headers = normalize_csv_headers(raw_headers, max_columns)
    else:
        data_rows = rows
        max_columns = max((len(r) for r in data_rows), default=0)
        if max_columns == 0:
            raise HTTPException(status_code=400, detail="CSV sem colunas validas.")
        if max_columns > MAX_CSV_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV com muitas colunas (max {MAX_CSV_COLUMNS}).",
            )
        headers = [f"col_{idx + 1}" for idx in range(max_columns)]

    parsed_rows: list[dict[str, str]] = []
    for row in data_rows:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        elif len(row) > len(headers):
            row = row[: len(headers)]
        for cell in row:
            if len(cell) > MAX_CELL_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Celula muito longa (max {MAX_CELL_CHARS} chars).",
                )
        parsed_rows.append(dict(zip(headers, row)))

    return headers, parsed_rows, dialect.delimiter


def build_html_table(
    columns: list[str],
    rows: list[dict[str, str]],
    caption: str | None = None,
) -> str:
    head_cells = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        values = [html.escape(str(row.get(col, ""))) for col in columns]
        cells = "".join(f"<td>{value}</td>" for value in values)
        body_rows.append(f"<tr>{cells}</tr>")
    caption_html = f"<caption>{html.escape(caption)}</caption>" if caption else ""
    table_html = (
        "<div class=\"table-wrap\">"
        "<table>"
        f"{caption_html}"
        "<thead><tr>"
        f"{head_cells}"
        "</tr></thead>"
        "<tbody>"
        f"{''.join(body_rows)}"
        "</tbody>"
        "</table>"
        "</div>"
    )
    return table_html


def render_html_from_spec(
    spec: TableSpec, include_header: bool = True
) -> str:
    table_html = build_html_table(spec.columns, spec.rows)
    if not include_header:
        return table_html
    title = spec.title.strip() if spec.title else "Relatorio CSV"
    description = spec.description.strip() if spec.description else ""
    parts = [f"<h1>{html.escape(title)}</h1>"]
    if description:
        parts.append(f"<p>{html.escape(description)}</p>")
    parts.append(table_html)
    return "\n".join(parts)


def normalize_header_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    ).lower()
    return re.sub(r"[\s_\-./]+", "", normalized)


def map_hoftalon_activity_columns(
    headers: list[str],
) -> tuple[dict[str, str] | None, str | None]:
    normalized_headers = {normalize_header_name(h): h for h in headers}
    mapping: dict[str, str] = {}
    for required in HOFTALON_ACTIVIDADES_COLUMNS:
        candidates = [required]
        candidates.extend(sorted(HOFTALON_ACTIVIDADES_SYNONYMS.get(required, set())))
        found = None
        for candidate in candidates:
            key = normalize_header_name(candidate)
            if key in normalized_headers:
                found = normalized_headers[key]
                break
        if not found:
            return None, f"Coluna obrigatoria ausente na tabela de atividades: {required}."
        mapping[required] = found
    return mapping, None


def build_hoftalon_activities_table(
    csv_text: str, delimiter: str | None, has_header: bool
) -> tuple[str, dict[str, Any]]:
    headers, rows, used_delimiter = parse_csv_text_strict(
        csv_text, delimiter, has_header
    )
    mapping, error = map_hoftalon_activity_columns(headers)
    if error:
        raise HTTPException(status_code=400, detail=error)
    mapped_rows = []
    for row in rows:
        mapped_rows.append(
            {required: row.get(mapping[required], "") for required in HOFTALON_ACTIVIDADES_COLUMNS}
        )
    table_html = build_html_table(HOFTALON_ACTIVIDADES_COLUMNS, mapped_rows)
    meta = {
        "columns": HOFTALON_ACTIVIDADES_COLUMNS,
        "row_count": len(rows),
        "delimiter": used_delimiter,
        "has_header": has_header,
        "sampled": False,
        "truncated": False,
        "mapped_columns": mapping,
    }
    return table_html, meta


def ensure_hoftalon_table_keys(tables: list["LLMTableRequest"]) -> str | None:
    return None


def validate_hoftalon_template(template_text: str) -> str | None:
    return None


def validate_hoftalon_data(data_obj: dict[str, Any]) -> str | None:
    custom_pages = data_obj.get("custom_pages")
    if custom_pages is None:
        return None
    if not isinstance(custom_pages, list):
        return "Campo 'custom_pages' deve ser uma lista."
    if len(custom_pages) > MAX_CUSTOM_PAGES:
        return f"Numero maximo de paginas (max {MAX_CUSTOM_PAGES})."
    for idx, page in enumerate(custom_pages, start=1):
        if not isinstance(page, dict):
            return f"Pagina {idx} invalida (deve ser objeto)."
        layout = page.get("layout", "page")
        if not isinstance(layout, str):
            return f"Layout invalido na pagina {idx}."
        layout_value = layout.strip().lower()
        if layout_value not in HOFTALON_CUSTOM_PAGE_LAYOUTS:
            return f"Layout invalido na pagina {idx}."
        content = page.get("content")
        if not isinstance(content, str) or not content.strip():
            return f"Conteudo vazio na pagina {idx}."
        if len(content) > MAX_CUSTOM_PAGE_CHARS:
            return (
                "Conteudo da pagina "
                f"{idx} muito longo (max {MAX_CUSTOM_PAGE_CHARS} chars)."
            )
    return None


def strip_html_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(text)


def contains_html_table(text: str) -> bool:
    return bool(re.search(r"<table\\b", text, flags=re.IGNORECASE))


def find_heading_match(html_text: str, level: int, title: str) -> re.Match | None:
    pattern = rf"<h{level}[^>]*>\\s*{re.escape(title)}\\s*</h{level}>"
    return re.search(pattern, html_text)


def find_heading_prefix_match(
    html_text: str, level: int, prefix: str
) -> re.Match | None:
    pattern = rf"<h{level}[^>]*>\\s*{re.escape(prefix)}\\b"
    return re.search(pattern, html_text)


def extract_section_by_match(
    html_text: str, start_match: re.Match | None, end_match: re.Match | None
) -> str:
    if not start_match:
        return ""
    start_idx = start_match.end()
    end_idx = end_match.start() if end_match else len(html_text)
    return html_text[start_idx:end_idx]


def extract_first_table_columns(text: str) -> list[str]:
    match = re.search(r"<table\\b.*?</table>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    table_html = match.group(0)
    thead_match = re.search(
        r"<thead\\b.*?</thead>", table_html, flags=re.IGNORECASE | re.DOTALL
    )
    target = thead_match.group(0) if thead_match else table_html
    headers = re.findall(r"<th[^>]*>(.*?)</th>", target, flags=re.IGNORECASE | re.DOTALL)
    cleaned = [strip_html_tags(header).strip() for header in headers]
    return [header for header in cleaned if header]


def find_forbidden_terms(text: str) -> list[str]:
    found = []
    for term in HOFTALON_FORBIDDEN_TERMS:
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(term)
    return found


def validate_hoftalon_output(
    output_html: str, tables_meta: list[dict[str, Any]]
) -> str | None:
    return None


def build_hoftalon_render_data(
    data_obj: dict[str, Any], tables_meta: list[dict[str, Any]]
) -> dict[str, Any]:
    render_data = dict(data_obj)
    render_data.setdefault("resultados_4_1_titulo", "Resultados - Tabela 1")
    render_data.setdefault("resultados_4_2_titulo", "Resultados - Tabela 2")
    render_data.setdefault("resultados_4_1_sufixo", "")
    render_data.setdefault("resultados_4_2_sufixo", "")
    render_data.setdefault("resultados_4_1_nota", "")
    render_data.setdefault("resultados_4_2_nota", "")
    render_data.setdefault("plano_intro", "")
    render_data.setdefault("logo_url", "")
    sampled_keys = {
        meta.get("key")
        for meta in tables_meta
        if meta.get("sampled") or meta.get("truncated")
    }
    if "resultados_1" in sampled_keys:
        render_data["resultados_4_1_sufixo"] = " (amostra)"
        render_data["resultados_4_1_nota"] = "Amostra: dados parciais."
    if "resultados_2" in sampled_keys:
        render_data["resultados_4_2_sufixo"] = " (amostra)"
        render_data["resultados_4_2_nota"] = "Amostra: dados parciais."
    sumario = [
        "1. OBJETIVO",
        "2. ESCOPO",
        "3. METODOLOGIA",
        "4. RESULTADOS",
        (
            "4.1 "
            + render_data.get("resultados_4_1_titulo", "").strip()
            + render_data.get("resultados_4_1_sufixo", "")
        ).strip(),
        (
            "4.2 "
            + render_data.get("resultados_4_2_titulo", "").strip()
            + render_data.get("resultados_4_2_sufixo", "")
        ).strip(),
        "5. PLANO DE AÇÃO",
        "6. ACHADOS / OBSERVAÇÕES",
    ]
    render_data.setdefault("sumario", sumario)
    render_data.setdefault("custom_pages_html", "")
    return render_data


def build_custom_pages_html(
    custom_pages: list[dict[str, Any]] | None, render_data: dict[str, Any]
) -> tuple[str | None, str | None]:
    if not custom_pages:
        return "", None

    blocks: list[str] = []
    for idx, page in enumerate(custom_pages, start=1):
        layout = page.get("layout", "page")
        content = page.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return None, f"Conteudo vazio na pagina {idx}."
        rendered, render_error = render_html_safe(content, render_data)
        if render_error:
            return None, f"Pagina {idx}: {render_error}"
        layout_value = (
            layout.strip().lower() if isinstance(layout, str) else "page"
        )
        if layout_value == "cover":
            block = f'<section class="print-cover">{rendered.strip()}</section>'
        elif layout_value == "toc":
            block = f'<section class="print-toc">{rendered.strip()}</section>'
        else:
            block = rendered.strip()
        blocks.append(block)

    pages_html = "\n<div class=\"page-break\"></div>\n".join(blocks)
    return pages_html, None


def prepare_custom_pages_data(
    data_obj: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    render_data = dict(data_obj)
    render_data.setdefault("custom_pages_html", "")
    render_data.setdefault("show_base_cover", True)
    render_data.setdefault("show_base_toc", True)
    custom_pages = render_data.get("custom_pages")
    if custom_pages:
        layouts = []
        for page in custom_pages:
            if isinstance(page, dict):
                layout = page.get("layout", "page")
                if isinstance(layout, str):
                    layouts.append(layout.strip().lower())
        if "cover" in layouts:
            render_data["show_base_cover"] = False
        if "toc" in layouts:
            render_data["show_base_toc"] = False
        custom_pages_html, custom_pages_error = build_custom_pages_html(
            custom_pages, render_data
        )
        if custom_pages_error:
            return None, custom_pages_error
        render_data["custom_pages_html"] = custom_pages_html
    return render_data, None


def validate_against_csv(
    spec: TableSpec, headers: list[str], rows: list[dict[str, str]]
) -> None:
    if spec.columns != headers:
        raise ValueError(
            "Colunas divergem do CSV.\n"
            f"CSV: {headers}\n"
            f"LLM: {spec.columns}"
        )

    if len(spec.rows) != len(rows):
        raise ValueError(
            "Quantidade de linhas diverge do CSV.\n"
            f"CSV: {len(rows)}\n"
            f"LLM: {len(spec.rows)}"
        )

    for idx, csv_row in enumerate(rows):
        llm_row = spec.rows[idx]
        missing = [col for col in headers if col not in llm_row]
        if missing:
            raise ValueError(f"Linha {idx}: faltam colunas {missing}.")
        extra = [col for col in llm_row.keys() if col not in headers]
        if extra:
            raise ValueError(f"Linha {idx}: colunas extras {extra}.")
        for col in headers:
            csv_value = csv_row.get(col, "")
            llm_value = llm_row.get(col, "")
            if llm_value != csv_value:
                raise ValueError(
                    "Conteudo diverge do CSV.\n"
                    f"Primeira diferenca em (linha={idx}, coluna='{col}'):\n"
                    f"CSV: {csv_value!r}\n"
                    f"LLM: {llm_value!r}"
                )


def normalize_table_key(value: str) -> tuple[str | None, str | None]:
    key = value.strip()
    if not key:
        return None, "Chave da tabela e obrigatoria."
    if len(key) > MAX_TEMPLATE_KEY_CHARS:
        return (
            None,
            f"Chave da tabela muito longa (max {MAX_TEMPLATE_KEY_CHARS} caracteres).",
        )
    return key, None


async def generate_llm_spec(
    csv_text: str,
    title_hint: str | None,
    description_hint: str | None,
    model: str | None,
    base_url: str | None,
    temperature: float | None,
) -> TableSpec:
    try:
        from langchain_core.output_parsers import PydanticOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_ollama import ChatOllama
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "Dependencias do LLM nao instaladas. "
                "Instale: langchain-core langchain-ollama."
            ),
        ) from exc

    parser = PydanticOutputParser(pydantic_object=TableSpec)
    system_prompt = (
        "Voce e um gerador de tabelas, mas voce DEVE "
        "responder no formato estruturado solicitado.\n\n"
        "Regras obrigatorias:\n"
        "- NAO invente colunas nem valores.\n"
        "- NAO reordene colunas.\n"
        "- NAO altere capitalizacao, acentuacao, pontuacao ou espacamento dos valores.\n"
        "- Todos os valores devem ser retornados como STRING, exatamente como no CSV.\n"
        "- A lista 'rows' deve ter exatamente o mesmo numero de linhas do CSV.\n"
        "- Cada objeto em 'rows' deve conter todas as colunas listadas em 'columns'.\n\n"
        "{format_instructions}"
    )

    user_lines = []
    if title_hint:
        user_lines.append(f"Use este titulo: {title_hint}")
    if description_hint:
        user_lines.append(f"Use esta descricao: {description_hint}")
    user_lines.append("Converta este CSV para a estrutura solicitada:")
    user_lines.append("")
    user_lines.append("{csv_content}")
    user_prompt = "\n".join(user_lines)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("user", user_prompt),
        ]
    ).partial(format_instructions=parser.get_format_instructions())

    model_value = model.strip() if model else LLM_DEFAULT_MODEL
    base_url_value = base_url.strip() if base_url else LLM_DEFAULT_BASE_URL
    temperature_value = LLM_DEFAULT_TEMPERATURE if temperature is None else temperature

    llm = ChatOllama(
        model=model_value,
        base_url=base_url_value,
        temperature=temperature_value,
    )

    def run_chain() -> TableSpec:
        return (prompt | llm | parser).invoke({"csv_content": csv_text})

    try:
        spec = await anyio.to_thread.run_sync(run_chain)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro no LLM: {exc}") from exc

    if title_hint:
        spec.title = title_hint
    if description_hint:
        spec.description = description_hint

    return spec


async def generate_llm_html_from_csv(
    csv_text: str,
    delimiter: str | None,
    has_header: bool,
    title_hint: str | None,
    description_hint: str | None,
    model: str | None,
    base_url: str | None,
    temperature: float | None,
    include_header: bool = True,
) -> tuple[str, dict[str, Any]]:
    headers, rows, used_delimiter = parse_csv_text_strict(
        csv_text, delimiter, has_header
    )
    spec = await generate_llm_spec(
        csv_text, title_hint, description_hint, model, base_url, temperature
    )
    validate_against_csv(spec, headers, rows)
    table_html = render_html_from_spec(spec, include_header=include_header)
    meta = {
        "columns": headers,
        "row_count": len(rows),
        "delimiter": used_delimiter,
        "has_header": has_header,
        "sampled": False,
        "truncated": False,
    }
    return table_html, meta


async def read_upload_file_limited(file: UploadFile, max_bytes: int) -> bytes:
    content = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"CSV muito grande (max {max_bytes} bytes).",
            )
    if not content:
        raise HTTPException(status_code=400, detail="Arquivo CSV vazio.")
    return bytes(content)


async def read_upload_file_limited_generic(
    file: UploadFile, max_bytes: int, label: str
) -> bytes:
    content = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} muito grande (max {max_bytes} bytes).",
            )
    if not content:
        raise HTTPException(status_code=400, detail=f"{label} vazio.")
    return bytes(content)


def save_report(
    session: Session,
    template: str,
    data_obj: dict[str, Any],
    output_html: str,
    template_record: Template | None = None,
) -> str | None:
    try:
        data_json = json.dumps(data_obj, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        return f"Erro ao salvar JSON: {exc}"
    report = Report(
        template_id=template_record.id if template_record else None,
        template_key=template_record.key if template_record else None,
        template_version=template_record.version if template_record else None,
        template=template,
        data_json=data_json,
        markdown=output_html,
    )
    session.add(report)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        return f"Erro ao salvar relatorio: {exc}"
    return None


def fetch_active_templates(session: Session) -> list[Template]:
    try:
        return session.exec(
            select(Template)
            .where(Template.is_active == True)
            .order_by(Template.key, Template.version.desc())
        ).all()
    except Exception:
        return []


def fetch_active_templates_safe(timeout_seconds: float = 0.5) -> list[Template]:
    def run_query() -> list[Template]:
        with Session(engine) as session:
            return fetch_active_templates(session)

    try:
        future = render_executor.submit(run_query)
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        return []
    except Exception:
        return []


def render_page_with_templates(session: Session, *args, **kwargs) -> str:
    return render_page(*args, templates=fetch_active_templates(session), **kwargs)


def render_nav(active_tab: str) -> str:
    generator_active = active_tab == "generator"
    templates_active = active_tab == "templates"
    reports_active = active_tab == "reports"
    generator_class = "tab active" if generator_active else "tab"
    templates_class = "tab active" if templates_active else "tab"
    reports_class = "tab active" if reports_active else "tab"
    generator_current = ' aria-current="page"' if generator_active else ""
    templates_current = ' aria-current="page"' if templates_active else ""
    reports_current = ' aria-current="page"' if reports_active else ""
    return (
        '<nav class="tabs" aria-label="Navegacao">'
        f'<a class="{generator_class}" href="/"{generator_current}>Gerador</a>'
        f'<a class="{templates_class}" href="/templates"{templates_current}>Templates</a>'
        f'<a class="{reports_class}" href="/reports"{reports_current}>Relatorios</a>'
        "</nav>"
    )


def render_template_preview(body: str, limit: int = 240) -> str:
    text = strip_html_tags(body)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def render_report_preview(body: str, limit: int = 260) -> str:
    return render_template_preview(body, limit=limit)


def render_html_preview(html_text: str) -> str:
    rendered = html_text.strip()
    rendered = re.sub(
        r'href="javascript:[^"]*"', 'href="#"', rendered, flags=re.IGNORECASE
    )
    rendered = re.sub(
        r'src="javascript:[^"]*"', 'src=""', rendered, flags=re.IGNORECASE
    )
    return rendered


def render_pdf_page(html_text: str, title: str = "Relatorio", auto_print: bool = True) -> str:
    output_rendered = render_html_preview(html_text)
    title_escaped = html.escape(title or "Relatorio")
    pdf_css = strip_css_imports(BASE_CSS)
    auto_print_script = ""
    if auto_print:
        auto_print_script = (
            "<script>"
            "window.addEventListener('load', () => {"
            "  window.print();"
            "});"
            "</script>"
        )
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{title_escaped}</title>
    <style>
{pdf_css}
{PDF_CSS}
    </style>
  </head>
  <body class="abnt-mode pdf-mode">
    <div class="markdown-preview">{output_rendered}</div>
    {auto_print_script}
  </body>
</html>
"""


def render_pdf_bytes(html_text: str, base_url: str, title: str) -> bytes:
    if HTML is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "PDF server-side indisponivel. Instale WeasyPrint: "
                "pip install weasyprint (requer dependencias do sistema)."
            ),
        )
    html = render_pdf_page(html_text, title=title, auto_print=False)
    return HTML(string=html, base_url=base_url).write_pdf()


def render_page(
    template_value: str,
    data_value: str,
    output: str | None = None,
    error: str | None = None,
    notice: str | None = None,
    template_key: str = "",
    template_version: str | int | None = None,
    template_id: str | int | None = None,
    templates: list[Template] | None = None,
) -> str:
    template_escaped = html.escape(template_value)
    data_escaped = html.escape(data_value)
    output_escaped = html.escape(output or "")
    template_key_escaped = html.escape(template_key)
    template_version_value = "" if template_version is None else str(template_version)
    template_version_escaped = html.escape(template_version_value)
    template_id_value = "" if template_id is None else str(template_id)
    template_id_escaped = html.escape(template_id_value)
    nav_html = render_nav("generator")
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    notice_html = f'<div class="notice">{html.escape(notice)}</div>' if notice else ""
    output_html = ""
    if output:
        output_rendered = render_html_preview(output)
        output_html = f"""
      <section class="card output-card">
        <div class="card-header">
          <h2>Saida</h2>
          <span class="badge accent">HTML</span>
          <button type="button" class="btn ghost copy-btn" data-copy-target="#html-raw">
            Copiar
          </button>
          <button type="button" class="btn ghost" id="html-preview-pdf-btn">
            Preview PDF
          </button>
          <button type="button" class="btn ghost print-btn" data-print-target="#html-preview">
            Imprimir/PDF
          </button>
          <span class="copy-status" id="copy-status" aria-live="polite"></span>
        </div>
        <div class="preview-frame">
          <div class="markdown-preview pdf-preview" id="html-preview">{output_rendered}</div>
          <iframe class="pdf-iframe" id="html-preview-pdf" title="Preview PDF"></iframe>
        </div>
        <details class="raw-output">
          <summary>Ver HTML bruto</summary>
          <pre class="code-block" id="html-raw">{output_escaped}</pre>
        </details>
      </section>
"""
    update_button_html = ""
    if template_id_value:
        update_button_html = (
            f'<button type="submit" class="btn ghost" '
            f'formaction="/templates/{template_id_value}/update">'
            "Atualizar template"
            "</button>"
        )
    template_link_notice = ""
    if template_id_value:
        template_link_notice = (
            '<p class="summary">Template ligado ao banco. '
            'Use "Atualizar template" para aplicar mudancas.</p>'
        )
    flow_template_example = html.escape(FLOW_TEMPLATE_EXAMPLE)
    flow_data_example = html.escape(FLOW_DATA_EXAMPLE)
    flow_table_csv_example = html.escape(FLOW_TABLE_CSV_EXAMPLE)
    flow_table_csv_example_2 = html.escape(FLOW_TABLE_CSV_EXAMPLE_2)
    flow_table_csv_example_actividades = html.escape(
        FLOW_TABLE_CSV_EXAMPLE_ACTIVIDADES
    )
    llm_default_model = html.escape(LLM_DEFAULT_MODEL)
    llm_default_base_url = html.escape(LLM_DEFAULT_BASE_URL)
    templates_list = templates or []
    template_options = ['<option value="">Manual (editar livre)</option>']
    for template in templates_list:
        label = f"{template.key} v{template.version}"
        selected = ""
        if template_id_value and str(template.id) == template_id_value:
            selected = " selected"
        template_options.append(
            f'<option value="{template.id}"{selected}>{html.escape(label)}</option>'
        )
    template_options_html = "\n".join(template_options)
    templates_payload = {
        str(template.id): {
            "key": template.key,
            "version": template.version,
        }
        for template in templates_list
    }
    templates_json = json.dumps(templates_payload, ensure_ascii=True).replace("<", "\\u003c")
    flow_example_payload = {
        "template": FLOW_TEMPLATE_EXAMPLE,
        "data": FLOW_DATA_EXAMPLE,
        "report_style": "hoftalon",
        "tables": [
            {
                "key": "resultados_1",
                "csv": FLOW_TABLE_CSV_EXAMPLE,
                "delimiter": ",",
                "has_header": True,
                "title": "Tabela de resultados 1",
                "description": "",
            },
            {
                "key": "resultados_2",
                "csv": FLOW_TABLE_CSV_EXAMPLE_2,
                "delimiter": ",",
                "has_header": True,
                "title": "Tabela de resultados 2",
                "description": "",
            },
            {
                "key": "atividades",
                "csv": FLOW_TABLE_CSV_EXAMPLE_ACTIVIDADES,
                "delimiter": ",",
                "has_header": True,
                "title": "Tabela de atividades",
                "description": "",
            },
        ],
    }
    flow_example_json = json.dumps(flow_example_payload, ensure_ascii=True).replace(
        "<", "\\u003c"
    )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Gerador de Relatorios</title>
    <style>
{BASE_CSS}
    </style>
  </head>
  <body class="abnt-mode">
    <main class="shell">
      {nav_html}
      <header class="hero">
        <div>
          <p class="eyebrow">MVP</p>
          <h1>Gerador de Relatorios</h1>
          <p class="lead">Escreva um template Jinja2 e dados JSON, depois gere o HTML.</p>
        </div>
      </header>
      <section class="layout">
        <section class="primary-column">
          <section class="card">
            <h2>Editor</h2>
            {notice_html}
            {error_html}
            {template_link_notice}
            <form method="post" action="/generate" class="stack">
              <input type="hidden" id="template_id" name="template_id" value="{template_id_escaped}">
              <div class="field">
                <label for="template_select">Templates ativos</label>
                <select id="template_select" name="template_select">
                  {template_options_html}
                </select>
                <p class="summary">Selecionar carrega o corpo e versao.</p>
              </div>
              <div class="field">
                <label for="template_key">Nome do template</label>
                <input
                  id="template_key"
                  name="template_key"
                  type="text"
                  placeholder="ex: relatorio_vendas"
                  value="{template_key_escaped}"
                >
              </div>
              <div class="field">
                <label for="template_version">Versao</label>
                <input
                  id="template_version"
                  name="template_version"
                  type="number"
                  min="1"
                  placeholder="ex: 1"
                  value="{template_version_escaped}"
                >
              </div>
              <div class="field">
                <label for="template">Template</label>
                <textarea id="template" name="template">{template_escaped}</textarea>
              </div>
              <div class="field">
                <label for="data">Dados (JSON)</label>
                <textarea id="data" name="data">{data_escaped}</textarea>
              </div>
              <div class="buttons">
                <button type="submit" class="btn primary">Gerar</button>
                <button
                  type="submit"
                  class="btn secondary"
                  formaction="/download/pdf"
                  formtarget="_blank"
                >
                  Baixar PDF
                </button>
                <button type="submit" class="btn ghost" formaction="/download">Baixar HTML</button>
                <button type="submit" class="btn ghost" formaction="/templates/save">Salvar template</button>
                {update_button_html}
              </div>
            </form>
          </section>
          {output_html}
          <section class="card">
            <h2>Relatorio com tabelas (LLM)</h2>
            <p class="summary">Monte o template com textos e injete tabelas geradas pelo LLM.</p>
            <form id="flow-form" class="stack" data-max-tables="{MAX_LLM_TABLES}">
              <div class="field">
                <label for="flow_template">Template</label>
                <textarea id="flow_template" name="flow_template">{flow_template_example}</textarea>
                <p class="summary">Use <code>{{ tables_html["chave"] }}</code> para inserir a tabela.</p>
              </div>
            <div class="field">
              <label for="flow_data">Dados (JSON)</label>
              <textarea id="flow_data" name="flow_data">{flow_data_example}</textarea>
              <p class="summary">Opcional: use <code>custom_pages</code> para inserir paginas completas (layout: cover, page ou toc).</p>
              <p class="summary">Logo privado: por padrao salva em <code>assets/logo.png</code> e usa <code>/private/logo</code> sem token. Se quiser, configure <code>PRIVATE_LOGO_PATH</code> ou ative <code>PRIVATE_LOGO_REQUIRE_TOKEN=1</code> com <code>PRIVATE_LOGO_TOKEN</code>.</p>
            </div>
            <div class="field">
              <label>Configuracao da capa</label>
              <div class="filters">
                <div class="field">
                  <label for="flow_logo_token">Token do logo</label>
                  <input id="flow_logo_token" type="text" placeholder="token (opcional se desativado)">
                </div>
                <div class="field">
                  <label for="flow_logo_file">Arquivo do logo</label>
                  <input id="flow_logo_file" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml">
                </div>
                <div class="field">
                  <label>&nbsp;</label>
                  <button type="button" class="btn ghost" id="flow_logo_upload">Enviar logo</button>
                </div>
              </div>
              <p class="summary" id="flow_logo_status"></p>
            </div>
            <div class="field">
              <label for="flow_style">Estilo</label>
              <select id="flow_style" name="flow_style">
                <option value="default">Padrao</option>
                <option value="hoftalon" selected>Hoftalon</option>
              </select>
              <p class="summary">Hoftalon aplica estilo ABNT e paginação, com estrutura livre.</p>
            </div>
            <div class="field">
              <label>Parametros do LLM</label>
              <div class="filters">
                  <div class="field">
                    <label for="flow_model">Modelo</label>
                    <input id="flow_model" name="flow_model" type="text" placeholder="{llm_default_model}">
                  </div>
                  <div class="field">
                    <label for="flow_base_url">Base URL</label>
                    <input id="flow_base_url" name="flow_base_url" type="text" placeholder="{llm_default_base_url}">
                  </div>
                  <div class="field">
                    <label for="flow_temperature">Temperatura</label>
                    <input id="flow_temperature" name="flow_temperature" type="number" step="0.1" min="0" placeholder="0">
                  </div>
                </div>
              </div>
              <div class="field">
                <label>
                  <input id="flow_append" type="checkbox">
                  Forcar anexar tabelas no final
                </label>
              </div>
              <div id="flow-error" class="error" style="display: none;"></div>
              <div class="table-list" id="flow-table-list">
                <div class="table-item" data-index="1">
                  <div class="table-item-header">
                    <p class="summary">Tabela 1</p>
                    <button type="button" class="btn ghost small remove-table">Remover</button>
                  </div>
                  <div class="field">
                    <label for="flow_table_key_1">Chave</label>
                    <input id="flow_table_key_1" class="table-key" type="text" value="resultados_1">
                  </div>
                  <div class="field">
                    <label for="flow_table_file_1">Arquivo CSV</label>
                    <input id="flow_table_file_1" class="table-file" type="file" accept=".csv,text/csv">
                    <p class="summary">Ao selecionar, o CSV sera preenchido abaixo.</p>
                  </div>
                  <div class="field">
                    <label for="flow_table_csv_1">CSV</label>
                    <textarea id="flow_table_csv_1" class="table-csv">{flow_table_csv_example}</textarea>
                  </div>
                  <div class="field">
                    <label for="flow_table_delimiter_1">Delimitador</label>
                    <input id="flow_table_delimiter_1" class="table-delimiter" type="text" placeholder="; , | ou tab" value=",">
                  </div>
                  <div class="field">
                    <label for="flow_table_header_1">Cabecalho</label>
                    <select id="flow_table_header_1" class="table-header">
                      <option value="true" selected>Sim</option>
                      <option value="false">Nao</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="flow_table_title_1">Titulo</label>
                    <input id="flow_table_title_1" class="table-title" type="text" value="Tabela de resultados 1">
                  </div>
                  <div class="field">
                    <label for="flow_table_description_1">Descricao</label>
                    <textarea id="flow_table_description_1" class="table-description" placeholder="Resumo da tabela."></textarea>
                  </div>
                </div>
                <div class="table-item" data-index="2">
                  <div class="table-item-header">
                    <p class="summary">Tabela 2</p>
                    <button type="button" class="btn ghost small remove-table">Remover</button>
                  </div>
                  <div class="field">
                    <label for="flow_table_key_2">Chave</label>
                    <input id="flow_table_key_2" class="table-key" type="text" value="resultados_2">
                  </div>
                  <div class="field">
                    <label for="flow_table_file_2">Arquivo CSV</label>
                    <input id="flow_table_file_2" class="table-file" type="file" accept=".csv,text/csv">
                    <p class="summary">Ao selecionar, o CSV sera preenchido abaixo.</p>
                  </div>
                  <div class="field">
                    <label for="flow_table_csv_2">CSV</label>
                    <textarea id="flow_table_csv_2" class="table-csv">{flow_table_csv_example_2}</textarea>
                  </div>
                  <div class="field">
                    <label for="flow_table_delimiter_2">Delimitador</label>
                    <input id="flow_table_delimiter_2" class="table-delimiter" type="text" placeholder="; , | ou tab" value=",">
                  </div>
                  <div class="field">
                    <label for="flow_table_header_2">Cabecalho</label>
                    <select id="flow_table_header_2" class="table-header">
                      <option value="true" selected>Sim</option>
                      <option value="false">Nao</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="flow_table_title_2">Titulo</label>
                    <input id="flow_table_title_2" class="table-title" type="text" value="Tabela de resultados 2">
                  </div>
                  <div class="field">
                    <label for="flow_table_description_2">Descricao</label>
                    <textarea id="flow_table_description_2" class="table-description" placeholder="Resumo da tabela."></textarea>
                  </div>
                </div>
                <div class="table-item" data-index="3">
                  <div class="table-item-header">
                    <p class="summary">Tabela 3</p>
                    <button type="button" class="btn ghost small remove-table">Remover</button>
                  </div>
                  <div class="field">
                    <label for="flow_table_key_3">Chave</label>
                    <input id="flow_table_key_3" class="table-key" type="text" value="atividades">
                  </div>
                  <div class="field">
                    <label for="flow_table_file_3">Arquivo CSV</label>
                    <input id="flow_table_file_3" class="table-file" type="file" accept=".csv,text/csv">
                    <p class="summary">Ao selecionar, o CSV sera preenchido abaixo.</p>
                  </div>
                  <div class="field">
                    <label for="flow_table_csv_3">CSV</label>
                    <textarea id="flow_table_csv_3" class="table-csv">{flow_table_csv_example_actividades}</textarea>
                  </div>
                  <div class="field">
                    <label for="flow_table_delimiter_3">Delimitador</label>
                    <input id="flow_table_delimiter_3" class="table-delimiter" type="text" placeholder="; , | ou tab" value=",">
                  </div>
                  <div class="field">
                    <label for="flow_table_header_3">Cabecalho</label>
                    <select id="flow_table_header_3" class="table-header">
                      <option value="true" selected>Sim</option>
                      <option value="false">Nao</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="flow_table_title_3">Titulo</label>
                    <input id="flow_table_title_3" class="table-title" type="text" value="Tabela de atividades">
                  </div>
                  <div class="field">
                    <label for="flow_table_description_3">Descricao</label>
                    <textarea id="flow_table_description_3" class="table-description" placeholder="Resumo da tabela."></textarea>
                  </div>
                </div>
            </div>
            <template id="flow-table-template">
              <div class="table-item" data-index="__index__">
                <div class="table-item-header">
                  <p class="summary">Tabela __index__</p>
                  <button type="button" class="btn ghost small remove-table">Remover</button>
                </div>
                <div class="field">
                  <label for="flow_table_key___index__">Chave</label>
                  <input id="flow_table_key___index__" class="table-key" type="text" placeholder="ex: resultados_1">
                </div>
                <div class="field">
                  <label for="flow_table_file___index__">Arquivo CSV</label>
                  <input id="flow_table_file___index__" class="table-file" type="file" accept=".csv,text/csv">
                  <p class="summary">Ao selecionar, o CSV sera preenchido abaixo.</p>
                </div>
                <div class="field">
                  <label for="flow_table_csv___index__">CSV</label>
                  <textarea id="flow_table_csv___index__" class="table-csv" placeholder="cole o CSV aqui"></textarea>
                </div>
                <div class="field">
                  <label for="flow_table_delimiter___index__">Delimitador</label>
                  <input id="flow_table_delimiter___index__" class="table-delimiter" type="text" placeholder="; , | ou tab">
                </div>
                <div class="field">
                  <label for="flow_table_header___index__">Cabecalho</label>
                  <select id="flow_table_header___index__" class="table-header">
                    <option value="true" selected>Sim</option>
                    <option value="false">Nao</option>
                  </select>
                </div>
                <div class="field">
                  <label for="flow_table_title___index__">Titulo</label>
                  <input id="flow_table_title___index__" class="table-title" type="text" placeholder="Titulo opcional">
                </div>
                <div class="field">
                  <label for="flow_table_description___index__">Descricao</label>
                  <textarea id="flow_table_description___index__" class="table-description" placeholder="Descricao opcional"></textarea>
                </div>
              </div>
            </template>
            <div class="buttons">
              <button type="button" class="btn ghost" id="flow-load-example">Carregar exemplo</button>
              <button type="button" class="btn ghost" id="flow-add-table">Adicionar tabela</button>
              <button type="submit" class="btn primary">Gerar</button>
            </div>
            <div class="flow-results">
              <div class="field">
                <label for="flow-output">HTML gerado</label>
                <pre class="code-block" id="flow-output"></pre>
              </div>
              <div class="field">
                <label for="flow-preview">Preview renderizado</label>
                <div class="preview-frame">
                  <div class="markdown-preview pdf-preview preview-empty" id="flow-preview">
                    Preview aparecera aqui.
                  </div>
                  <iframe class="pdf-iframe" id="flow-preview-pdf" title="Preview PDF"></iframe>
                </div>
                <div class="buttons">
                  <button
                    type="button"
                    class="btn ghost small print-btn"
                    data-print-target="#flow-preview"
                  >
                    Imprimir/PDF
                  </button>
                  <button type="button" class="btn ghost small" id="flow-preview-pdf-btn">
                    Preview PDF
                  </button>
                  <button type="button" class="btn ghost small" id="flow-pdf">
                    Baixar PDF
                  </button>
                </div>
              </div>
            </div>
          </form>
        </section>
      </section>
    </section>
    </main>
    <script type="application/json" id="template-data">{templates_json}</script>
    <script type="application/json" id="flow-example-data">{flow_example_json}</script>
    <script>
      (() => {{
        const copyBtn = document.querySelector(".copy-btn");
        if (copyBtn) {{
          const statusEl = document.getElementById("copy-status");
          const targetSelector = copyBtn.getAttribute("data-copy-target");
          const target = targetSelector ? document.querySelector(targetSelector) : null;

          const setStatus = (message) => {{
            if (!statusEl) return;
            statusEl.textContent = message;
            window.setTimeout(() => {{
              if (statusEl.textContent === message) {{
                statusEl.textContent = "";
              }}
            }}, 2000);
          }};

          copyBtn.addEventListener("click", async () => {{
            if (!target) {{
              setStatus("Nada para copiar.");
              return;
            }}
            const text = target.textContent || "";
            try {{
              await navigator.clipboard.writeText(text);
              setStatus("Copiado.");
            }} catch (err) {{
              setStatus("Falha ao copiar.");
            }}
          }});
        }}

        const printButtons = document.querySelectorAll(".print-btn");
        if (printButtons.length) {{
          const clearPrintTargets = () => {{
            document.querySelectorAll(".print-target").forEach((el) => {{
              el.classList.remove("print-target");
            }});
          }};
          printButtons.forEach((btn) => {{
            btn.addEventListener("click", () => {{
              const targetSelector = btn.getAttribute("data-print-target");
              if (!targetSelector) return;
              const target = document.querySelector(targetSelector);
              if (!target) return;
              clearPrintTargets();
              target.classList.add("print-target");
              window.print();
            }});
          }});
        }}

        const loadPdfPreview = async (htmlContent, iframeId, htmlId) => {{
          const iframe = document.getElementById(iframeId);
          if (!iframe) return;
          if (!htmlContent || !htmlContent.trim()) return;
          const htmlPreview = htmlId ? document.getElementById(htmlId) : null;
          try {{
            const response = await fetch("/api/pdf", {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
              }},
              body: JSON.stringify({{
                html: htmlContent,
                title: "Relatorio",
              }}),
            }});
            if (!response.ok) return;
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            if (iframe.dataset.previewUrl) {{
              URL.revokeObjectURL(iframe.dataset.previewUrl);
            }}
            iframe.dataset.previewUrl = url;
            iframe.src = url;
            iframe.style.display = "block";
            if (htmlPreview) {{
              htmlPreview.style.display = "none";
            }}
          }} catch (err) {{
            // fallback to HTML preview if PDF fails
          }}
        }};

        const templateSelect = document.getElementById("template_select");
        const templateIdInput = document.getElementById("template_id");
        const templateKeyInput = document.getElementById("template_key");
        const templateVersionInput = document.getElementById("template_version");
        const templateBodyInput = document.getElementById("template");
        const dataEl = document.getElementById("template-data");
        let templateData = {{}};
        let activeTemplateId = null;
        let activeTemplateBody = null;
        if (dataEl && dataEl.textContent) {{
          try {{
            templateData = JSON.parse(dataEl.textContent);
          }} catch (err) {{
            templateData = {{}};
          }}
        }}

        const applyTemplate = async (templateId) => {{
          const selected = templateData[templateId];
          if (!selected) {{
            return;
          }}
          activeTemplateId = templateId;
          activeTemplateBody = selected.body || "";
          if (templateIdInput) {{
            templateIdInput.value = templateId;
          }}
          if (templateKeyInput) {{
            templateKeyInput.value = selected.key || "";
          }}
          if (templateVersionInput) {{
            templateVersionInput.value = selected.version || "";
          }}
          if (templateBodyInput && selected.body) {{
            templateBodyInput.value = selected.body || "";
            return;
          }}
          if (!templateBodyInput) return;
          try {{
            const response = await fetch(`/api/templates/${{templateId}}`);
            if (!response.ok) return;
            const body = await response.json();
            const templateBody = body.body || "";
            activeTemplateBody = templateBody;
            templateBodyInput.value = templateBody;
          }} catch (err) {{
            // ignore
          }}
        }};

        if (templateSelect) {{
          templateSelect.addEventListener("change", () => {{
            const selectedId = templateSelect.value;
            if (!selectedId) {{
              if (templateIdInput) {{
                templateIdInput.value = "";
              }}
              activeTemplateId = null;
              activeTemplateBody = null;
              return;
            }}
            applyTemplate(selectedId);
          }});

          if (templateSelect.value) {{
            applyTemplate(templateSelect.value);
          }}
        }}

        if (templateBodyInput) {{
          templateBodyInput.addEventListener("input", () => {{
            if (!activeTemplateId) return;
            if (templateBodyInput.value !== activeTemplateBody) {{
              if (templateIdInput) {{
                templateIdInput.value = "";
              }}
              if (templateSelect) {{
                templateSelect.value = "";
              }}
              activeTemplateId = null;
              activeTemplateBody = null;
            }}
          }});
        }}

        const rawHtml = document.getElementById("html-raw");
        const htmlPreviewBtn = document.getElementById("html-preview-pdf-btn");
        if (rawHtml && htmlPreviewBtn) {{
          htmlPreviewBtn.addEventListener("click", () => {{
            loadPdfPreview(rawHtml.textContent || "", "html-preview-pdf", "html-preview");
          }});
        }}

        const flowForm = document.getElementById("flow-form");
        if (flowForm) {{
          const tableList = document.getElementById("flow-table-list");
          const addTableBtn = document.getElementById("flow-add-table");
          const exampleBtn = document.getElementById("flow-load-example");
          const templateEl = document.getElementById("flow-table-template");
          const errorEl = document.getElementById("flow-error");
          const outputEl = document.getElementById("flow-output");
          const pdfBtn = document.getElementById("flow-pdf");
          const previewPdfBtn = document.getElementById("flow-preview-pdf-btn");
          const dataInput = document.getElementById("flow_data");
          const exampleDataEl = document.getElementById("flow-example-data");
          const logoTokenInput = document.getElementById("flow_logo_token");
          const logoFileInput = document.getElementById("flow_logo_file");
          const logoUploadBtn = document.getElementById("flow_logo_upload");
          const logoStatusEl = document.getElementById("flow_logo_status");
          const maxTables = parseInt(flowForm.dataset.maxTables || "0", 10) || 0;
          let tableCounter = tableList ? tableList.children.length : 0;
          let lastFlowHtml = "";
          let flowExampleData = null;
          if (exampleDataEl && exampleDataEl.textContent) {{
            try {{
              flowExampleData = JSON.parse(exampleDataEl.textContent);
            }} catch (err) {{
              flowExampleData = null;
            }}
          }}

          const showError = (message) => {{
            if (!errorEl) return;
            if (!message) {{
              errorEl.style.display = "none";
              errorEl.textContent = "";
              return;
            }}
            errorEl.style.display = "block";
            errorEl.textContent = message;
          }};

          const setLogoStatus = (message) => {{
            if (!logoStatusEl) return;
            logoStatusEl.textContent = message || "";
          }};

          const openPdfPreview = async () => {{
            if (!lastFlowHtml) {{
              showError("Gere o relatorio antes de baixar o PDF.");
              return;
            }}
            try {{
              const response = await fetch("/api/pdf", {{
                method: "POST",
                headers: {{
                  "Content-Type": "application/json",
                }},
                body: JSON.stringify({{
                  html: lastFlowHtml,
                  title: "Relatorio",
                }}),
              }});
              if (!response.ok) {{
                const body = await response.json().catch(() => ({{}}));
                showError(body.detail || "Falha ao gerar PDF.");
                return;
              }}
              const blob = await response.blob();
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = "relatorio.pdf";
              document.body.appendChild(link);
              link.click();
              link.remove();
              URL.revokeObjectURL(url);
            }} catch (err) {{
              showError("Falha ao gerar PDF.");
            }}
          }};

          const updateLogoUrlInJson = (logoUrl) => {{
            if (!dataInput) return false;
            const raw = dataInput.value.trim();
            let obj = {{}};
            if (raw) {{
              try {{
                obj = JSON.parse(raw);
              }} catch (err) {{
                showError("JSON invalido nos dados do relatorio.");
                return false;
              }}
            }}
            obj.logo_url = logoUrl;
            dataInput.value = JSON.stringify(obj, null, 2);
            return true;
          }};

          const buildTablePayload = () => {{
            const items = tableList ? Array.from(tableList.querySelectorAll(".table-item")) : [];
            return items.map((item) => {{
              const getValue = (selector) => {{
                const el = item.querySelector(selector);
                return el ? el.value : "";
              }};
              const csvValue = getValue(".table-csv");
              return {{
                key: getValue(".table-key"),
                csv: csvValue,
                delimiter: getValue(".table-delimiter"),
                has_header: getValue(".table-header") === "true",
                title: getValue(".table-title"),
                description: getValue(".table-description"),
              }};
            }});
          }};

          const detectDelimiter = (text) => {{
            const firstLine = text.split(/\\r?\\n/)[0] || "";
            const candidates = [",", ";", "|", "\\t"];
            let best = "";
            let bestCount = 0;
            candidates.forEach((delim) => {{
              const count = firstLine.split(delim).length - 1;
              if (count > bestCount) {{
                bestCount = count;
                best = delim;
              }}
            }});
            if (bestCount <= 0) return "";
            return best === "\\t" ? "tab" : best;
          }};

          const addTableItem = () => {{
            if (!tableList || !templateEl) return;
            if (maxTables && tableList.children.length >= maxTables) {{
              showError("Numero maximo de tabelas: " + maxTables + ".");
              return;
            }}
            tableCounter += 1;
            const html = templateEl.innerHTML.replace(/__index__/g, String(tableCounter));
            const wrapper = document.createElement("div");
            wrapper.innerHTML = html.trim();
            const item = wrapper.firstElementChild;
            if (!item) return;
            tableList.appendChild(item);
            return item;
          }};

          const applyExample = () => {{
            if (!flowExampleData) return;
            const templateInput = document.getElementById("flow_template");
            const styleInput = document.getElementById("flow_style");
            if (templateInput) {{
              templateInput.value = flowExampleData.template || "";
            }}
            if (dataInput) {{
              dataInput.value = flowExampleData.data || "";
            }}
            if (styleInput) {{
              styleInput.value = flowExampleData.report_style || "hoftalon";
            }}
            if (tableList) {{
              tableList.innerHTML = "";
              tableCounter = 0;
              const tables = Array.isArray(flowExampleData.tables) ? flowExampleData.tables : [];
              tables.forEach((table) => {{
                const item = addTableItem();
                if (!item) return;
                const setValue = (selector, value) => {{
                  const el = item.querySelector(selector);
                  if (el) {{
                    el.value = value ?? "";
                  }}
                }};
                setValue(".table-key", table.key || "");
                setValue(".table-csv", table.csv || "");
                setValue(".table-delimiter", table.delimiter || "");
                const headerValue = table.has_header === false ? "false" : "true";
                const headerEl = item.querySelector(".table-header");
                if (headerEl) {{
                  headerEl.value = headerValue;
                }}
                setValue(".table-title", table.title || "");
                setValue(".table-description", table.description || "");
              }});
            }}
            if (outputEl) outputEl.textContent = "";
            const previewEl = document.getElementById("flow-preview");
            if (previewEl) {{
              previewEl.classList.add("preview-empty");
              previewEl.textContent = "Preview aparecera aqui.";
            }}
          }};

          if (addTableBtn) {{
            addTableBtn.addEventListener("click", () => {{
              showError("");
              addTableItem();
            }});
          }}
          if (exampleBtn) {{
            exampleBtn.addEventListener("click", () => {{
              showError("");
              applyExample();
            }});
          }}

          if (tableList) {{
            tableList.addEventListener("click", (event) => {{
              const target = event.target;
              if (!(target instanceof HTMLElement)) return;
              if (target.classList.contains("remove-table")) {{
                const item = target.closest(".table-item");
                if (item) {{
                  item.remove();
                }}
              }}
            }});
            tableList.addEventListener("change", async (event) => {{
              const target = event.target;
              if (!(target instanceof HTMLInputElement)) return;
              if (!target.classList.contains("table-file")) return;
              const file = target.files && target.files[0];
              if (!file) return;
              try {{
                const text = await file.text();
                const item = target.closest(".table-item");
                if (!item) return;
                const csvArea = item.querySelector(".table-csv");
                if (csvArea) {{
                  csvArea.value = text;
                }}
                const delimiterInput = item.querySelector(".table-delimiter");
                if (delimiterInput && !delimiterInput.value.trim()) {{
                  const detected = detectDelimiter(text);
                  if (detected) {{
                    delimiterInput.value = detected;
                  }}
                }}
              }} catch (err) {{
                showError("Falha ao ler o CSV.");
              }}
            }});
          }}

          if (logoUploadBtn) {{
            logoUploadBtn.addEventListener("click", async () => {{
              showError("");
              setLogoStatus("");
              const token = logoTokenInput ? logoTokenInput.value.trim() : "";
              if (!logoFileInput || !logoFileInput.files || !logoFileInput.files[0]) {{
                showError("Selecione um arquivo de logo.");
                return;
              }}
              const file = logoFileInput.files[0];
              const formData = new FormData();
              formData.append("file", file);
              try {{
                const query = token ? `?token=${{encodeURIComponent(token)}}` : "";
                const response = await fetch(`/private/logo/upload${{query}}`, {{
                  method: "POST",
                  body: formData,
                }});
                const body = await response.json();
                if (!response.ok) {{
                  showError(body.detail || "Falha ao enviar logo.");
                  return;
                }}
                const logoUrl = body.logo_url || (token ? `/private/logo?token=${{token}}` : "/private/logo");
                if (updateLogoUrlInJson(logoUrl)) {{
                  setLogoStatus("Logo enviado e aplicado.");
                }}
              }} catch (err) {{
                showError("Falha ao enviar logo.");
              }}
            }});
          }}

          if (pdfBtn) {{
            pdfBtn.addEventListener("click", () => {{
              showError("");
              openPdfPreview();
            }});
          }}
          if (previewPdfBtn) {{
            previewPdfBtn.addEventListener("click", () => {{
              showError("");
              if (!lastFlowHtml) {{
                showError("Gere o relatorio antes do preview.");
                return;
              }}
              loadPdfPreview(lastFlowHtml, "flow-preview-pdf", "flow-preview");
            }});
          }}

          flowForm.addEventListener("submit", async (event) => {{
            event.preventDefault();
            showError("");
            if (outputEl) outputEl.textContent = "";

            const templateInput = document.getElementById("flow_template");
            const styleInput = document.getElementById("flow_style");
            const modelInput = document.getElementById("flow_model");
            const baseUrlInput = document.getElementById("flow_base_url");
            const tempInput = document.getElementById("flow_temperature");
            const appendInput = document.getElementById("flow_append");

            let dataObj = {{}};
            const rawData = dataInput ? dataInput.value.trim() : "";
            if (rawData) {{
              try {{
                dataObj = JSON.parse(rawData);
              }} catch (err) {{
                showError("JSON invalido nos dados do relatorio.");
                return;
              }}
            }}

            const payload = {{
              template: templateInput ? templateInput.value : "",
              data: dataObj,
              tables: buildTablePayload(),
            }};

            if (modelInput && modelInput.value.trim()) {{
              payload.model = modelInput.value.trim();
            }}
            if (styleInput && styleInput.value.trim()) {{
              payload.report_style = styleInput.value.trim();
            }}
            if (baseUrlInput && baseUrlInput.value.trim()) {{
              payload.base_url = baseUrlInput.value.trim();
            }}
            if (tempInput && tempInput.value.trim()) {{
              payload.temperature = Number(tempInput.value);
            }}
            if (appendInput && appendInput.checked) {{
              payload.append_tables = true;
            }}

            try {{
              const response = await fetch("/api/render_with_tables", {{
                method: "POST",
                headers: {{
                  "Content-Type": "application/json",
                }},
                body: JSON.stringify(payload),
              }});
              const body = await response.json();
              if (!response.ok) {{
                showError(body.detail || "Erro ao gerar relatorio.");
                return;
              }}
              const htmlOutput = body.html || "";
              lastFlowHtml = htmlOutput;
              if (outputEl) {{
                outputEl.textContent = htmlOutput;
              }}
              const previewEl = document.getElementById("flow-preview");
              if (previewEl) {{
                previewEl.classList.add("preview-empty");
                previewEl.textContent = "Carregando preview...";
              }}
              if (previewEl) {{
                try {{
                  const previewResponse = await fetch("/api/html/preview", {{
                    method: "POST",
                    headers: {{
                      "Content-Type": "application/json",
                    }},
                    body: JSON.stringify({{ html: htmlOutput }}),
                  }});
                  const previewBody = await previewResponse.json();
                  if (previewResponse.ok && previewBody.html !== undefined) {{
                    previewEl.innerHTML = previewBody.html || "";
                    previewEl.classList.remove("preview-empty");
                  }} else {{
                    previewEl.textContent = "Nao foi possivel renderizar o preview.";
                  }}
                }} catch (err) {{
                  previewEl.textContent = "Falha ao renderizar o preview.";
                }}
              }}
              // Preview PDF apenas sob demanda.
            }} catch (err) {{
              showError("Falha ao chamar o endpoint.");
            }}
          }});
        }}
      }})();
    </script>
  </body>
</html>
"""


def render_templates_page(
    templates: list[Template],
    q: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    total: int = 0,
    total_pages: int = 1,
    error: str | None = None,
) -> str:
    nav_html = render_nav("templates")
    q_value = q or ""
    status_value = status or ""
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    summary_html = render_summary(total, page, per_page)
    status_all_selected = "selected" if not status_value else ""
    status_active_selected = "selected" if status_value == "active" else ""
    status_inactive_selected = "selected" if status_value == "inactive" else ""
    per_page_value = str(per_page)
    per_page_10 = "selected" if per_page_value == "10" else ""
    per_page_20 = "selected" if per_page_value == "20" else ""
    per_page_50 = "selected" if per_page_value == "50" else ""
    filters_html = f"""
      <section class="card">
        <h2>Filtros</h2>
        {error_html}
        <form method="get" class="filters">
          <div class="field">
            <label for="q">Busca</label>
            <input id="q" name="q" type="text" placeholder="nome ou conteudo" value="{html.escape(q_value)}">
          </div>
          <div class="field">
            <label for="status">Status</label>
            <select id="status" name="status">
              <option value="" {status_all_selected}>Todos</option>
              <option value="active" {status_active_selected}>Ativos</option>
              <option value="inactive" {status_inactive_selected}>Inativos</option>
            </select>
          </div>
          <div class="field">
            <label for="per_page">Por pagina</label>
            <select id="per_page" name="per_page">
              <option value="10" {per_page_10}>10</option>
              <option value="20" {per_page_20}>20</option>
              <option value="50" {per_page_50}>50</option>
            </select>
          </div>
          <div class="buttons">
            <button class="btn primary" type="submit">Aplicar</button>
            <a class="btn ghost" href="/templates">Limpar</a>
          </div>
        </form>
        <p class="summary">{summary_html}</p>
      </section>
"""
    if not templates:
        list_html = '<div class="empty">Nenhum template cadastrado ainda.</div>'
    else:
        items = []
        for index, template in enumerate(templates, start=1):
            key = html.escape(template.key)
            status_text = "ativo" if template.is_active else "inativo"
            badge_class = "badge active" if template.is_active else "badge inactive"
            created_at_value = (
                template.created_at.isoformat() if template.created_at else "n/a"
            )
            created_at = html.escape(created_at_value)
            preview = html.escape(render_template_preview(template.body))
            open_html = ""
            action_html = ""
            if template.is_active:
                open_html = f'<a class="btn secondary" href="/templates/{template.id}">Abrir</a>'
                action_html = (
                    f'<form method="post" action="/templates/{template.id}/deactivate">'
                    '<button type="submit" class="btn danger">Desativar</button>'
                    "</form>"
                )
            else:
                open_html = '<span class="btn ghost disabled">Abrir</span>'
                action_html = (
                    f'<form method="post" action="/templates/{template.id}/activate">'
                    '<button type="submit" class="btn primary">Ativar</button>'
                    "</form>"
                )
            items.append(
                f"""
        <article class="template-card" style="--delay: {index}">
          <div class="template-header">
            <div>
              <p class="template-title">{key} <span class="{badge_class}">{status_text}</span></p>
              <div class="template-meta">v{template.version} - Criado {created_at}</div>
            </div>
            <div class="actions">
              {open_html}
              {action_html}
            </div>
          </div>
          <pre class="code-block">{preview}</pre>
        </article>
        """
            )
        list_html = "".join(items)

    pagination_html = render_pagination(
        "/templates",
        page,
        total_pages,
        {"q": q_value, "status": status_value, "per_page": per_page},
    )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Templates</title>
    <style>
{BASE_CSS}
    </style>
  </head>
  <body>
    <main class="shell">
      {nav_html}
      <header class="hero">
        <div>
          <p class="eyebrow">Biblioteca</p>
          <h1>Templates</h1>
          <p class="lead">Lista de templates salvos no banco.</p>
        </div>
      </header>
      {filters_html}
      <section class="template-grid">
        {list_html}
      </section>
      {pagination_html}
    </main>
  </body>
</html>
"""


def render_reports_page(
    reports: list[Report],
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    total: int = 0,
    total_pages: int = 1,
    error: str | None = None,
) -> str:
    nav_html = render_nav("reports")
    q_value = q or ""
    date_from_value = date_from or ""
    date_to_value = date_to or ""
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    summary_html = render_summary(total, page, per_page)
    per_page_value = str(per_page)
    per_page_10 = "selected" if per_page_value == "10" else ""
    per_page_20 = "selected" if per_page_value == "20" else ""
    per_page_50 = "selected" if per_page_value == "50" else ""
    filters_html = f"""
      <section class="card">
        <h2>Filtros</h2>
        {error_html}
        <form method="get" class="filters">
          <div class="field">
            <label for="q">Busca</label>
            <input id="q" name="q" type="text" placeholder="template ou saida" value="{html.escape(q_value)}">
          </div>
          <div class="field">
            <label for="date_from">De</label>
            <input id="date_from" name="date_from" type="date" value="{html.escape(date_from_value)}">
          </div>
          <div class="field">
            <label for="date_to">Ate</label>
            <input id="date_to" name="date_to" type="date" value="{html.escape(date_to_value)}">
          </div>
          <div class="field">
            <label for="per_page">Por pagina</label>
            <select id="per_page" name="per_page">
              <option value="10" {per_page_10}>10</option>
              <option value="20" {per_page_20}>20</option>
              <option value="50" {per_page_50}>50</option>
            </select>
          </div>
          <div class="buttons">
            <button class="btn primary" type="submit">Aplicar</button>
            <a class="btn ghost" href="/reports">Limpar</a>
          </div>
        </form>
        <p class="summary">{summary_html}</p>
      </section>
"""
    if not reports:
        list_html = '<div class="empty">Nenhum relatorio gerado ainda.</div>'
    else:
        items = []
        for index, report in enumerate(reports, start=1):
            created_at_value = (
                report.created_at.isoformat() if report.created_at else "n/a"
            )
            created_at = html.escape(created_at_value)
            preview = html.escape(render_report_preview(report.markdown))
            template_size = len(report.template)
            data_size = len(report.data_json)
            html_size = len(report.markdown)
            template_ref = ""
            if report.template_key and report.template_version is not None:
                template_ref = (
                    f"<div class=\"template-meta\">"
                    f"Template {html.escape(report.template_key)} v{report.template_version}"
                    "</div>"
                )
            items.append(
                f"""
        <article class="template-card" style="--delay: {index}">
          <div class="template-header">
            <div>
              <p class="template-title">Relatorio #{report.id}</p>
              <div class="template-meta">Criado {created_at}</div>
              {template_ref}
              <div class="template-meta">T {template_size} - J {data_size} - H {html_size}</div>
            </div>
            <div class="actions">
              <a class="btn secondary" href="/reports/{report.id}">Abrir</a>
              <a class="btn primary" href="/reports/{report.id}/pdf" target="_blank">PDF</a>
              <a class="btn ghost" href="/reports/{report.id}/download">HTML</a>
            </div>
          </div>
          <pre class="code-block">{preview}</pre>
        </article>
        """
            )
        list_html = "".join(items)

    pagination_html = render_pagination(
        "/reports",
        page,
        total_pages,
        {
            "q": q_value,
            "date_from": date_from_value,
            "date_to": date_to_value,
            "per_page": per_page,
        },
    )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Relatorios</title>
    <style>
{BASE_CSS}
    </style>
  </head>
  <body>
    <main class="shell">
      {nav_html}
      <header class="hero">
        <div>
          <p class="eyebrow">Historico</p>
          <h1>Relatorios</h1>
          <p class="lead">Relatorios gerados e salvos no banco.</p>
        </div>
      </header>
      {filters_html}
      <section class="template-grid">
        {list_html}
      </section>
      {pagination_html}
    </main>
  </body>
</html>
"""


def _init_db_background() -> None:
    try:
        init_db()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_init_db_background, daemon=True).start()
    yield


app = FastAPI(
    title="Report Generator",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
def read_root() -> str:
    templates = fetch_active_templates_safe()
    return render_page(DEFAULT_TEMPLATE, DEFAULT_DATA, templates=templates)


@app.get("/templates", response_class=HTMLResponse)
def list_templates(
    session: Session = Depends(get_session),
    q: str | None = None,
    status: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
) -> HTMLResponse:
    page_value, per_page_value = clamp_pagination(page, per_page)
    status_value = status if status in ("active", "inactive") else ""
    filters = []
    q_value = (q or "").strip()
    if q_value:
        like_value = f"%{q_value}%"
        filters.append(or_(Template.key.ilike(like_value), Template.body.ilike(like_value)))
    if status_value:
        is_active = status_value == "active"
        filters.append(Template.is_active == is_active)

    count_stmt = select(func.count()).select_from(Template)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = session.exec(count_stmt).one()
    total_pages = max(1, ceil(total / per_page_value)) if total else 1
    if page_value > total_pages:
        page_value = total_pages

    stmt = select(Template).order_by(Template.key, Template.version.desc())
    if filters:
        stmt = stmt.where(*filters)
    templates = session.exec(
        stmt.offset((page_value - 1) * per_page_value).limit(per_page_value)
    ).all()
    return HTMLResponse(
        render_templates_page(
            templates,
            q=q_value,
            status=status_value,
            page=page_value,
            per_page=per_page_value,
            total=total,
            total_pages=total_pages,
        )
    )


@app.get("/reports", response_class=HTMLResponse)
def list_reports(
    session: Session = Depends(get_session),
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
) -> HTMLResponse:
    page_value, per_page_value = clamp_pagination(page, per_page)
    filters = []
    q_value = (q or "").strip()
    if q_value:
        like_value = f"%{q_value}%"
        filters.append(
            or_(
                Report.template.ilike(like_value),
                Report.markdown.ilike(like_value),
                Report.data_json.ilike(like_value),
                Report.template_key.ilike(like_value),
            )
        )

    error_message = None
    from_value, from_error = parse_date_value(date_from)
    to_value, to_error = parse_date_value(date_to)
    if from_error:
        error_message = from_error
    if to_error:
        error_message = to_error if not error_message else f"{error_message} {to_error}"
    if from_value and to_value and from_value > to_value:
        error_message = "Data inicial nao pode ser maior que data final."

    if from_value:
        start_dt = datetime.combine(from_value, datetime.min.time())
        filters.append(Report.created_at >= start_dt)
    if to_value:
        end_dt = datetime.combine(to_value, datetime.max.time())
        filters.append(Report.created_at <= end_dt)

    count_stmt = select(func.count()).select_from(Report)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = session.exec(count_stmt).one()
    total_pages = max(1, ceil(total / per_page_value)) if total else 1
    if page_value > total_pages:
        page_value = total_pages

    stmt = select(Report).order_by(Report.created_at.desc())
    if filters:
        stmt = stmt.where(*filters)
    reports = session.exec(
        stmt.offset((page_value - 1) * per_page_value).limit(per_page_value)
    ).all()

    return HTMLResponse(
        render_reports_page(
            reports,
            q=q_value,
            date_from=date_from,
            date_to=date_to,
            page=page_value,
            per_page=per_page_value,
            total=total,
            total_pages=total_pages,
            error=error_message,
        )
    )


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def open_report(
    report_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Relatorio nao encontrado.")
    template_version_value = (
        str(report.template_version) if report.template_version is not None else ""
    )
    template_id_value = None
    if report.template_id:
        template = session.get(Template, report.template_id)
        if template and template.is_active:
            template_id_value = str(template.id)
    return HTMLResponse(
        render_page_with_templates(
            session,
            report.template,
            report.data_json,
            output=report.markdown,
            template_key=report.template_key or "",
            template_version=template_version_value,
            template_id=template_id_value,
        )
    )


@app.get("/reports/{report_id}/download")
def download_report(
    report_id: int, session: Session = Depends(get_session)
) -> Response:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Relatorio nao encontrado.")
    filename = f"relatorio_{report.id}.html"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=report.markdown,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


@app.get("/reports/{report_id}/pdf")
def download_report_pdf(
    report_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Relatorio nao encontrado.")
    title = f"Relatorio {report.id}"
    pdf_bytes = render_pdf_bytes(
        report.markdown, base_url=str(request.base_url), title=title
    )
    filename = f"relatorio_{report.id}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=pdf_bytes, media_type="application/pdf", headers=headers
    )


@app.get("/templates/{template_id}", response_class=HTMLResponse)
def open_template(
    template_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    template = session.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template nao encontrado.")
    if not template.is_active:
        raise HTTPException(status_code=403, detail="Template desativado.")
    return HTMLResponse(
        render_page_with_templates(
            session,
            template.body,
            DEFAULT_DATA,
            template_key=template.key,
            template_version=str(template.version),
            template_id=str(template.id),
        )
    )


@app.get("/api/templates/{template_id}")
def get_template_json(
    template_id: int, session: Session = Depends(get_session)
) -> dict[str, Any]:
    template = session.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template nao encontrado.")
    if not template.is_active:
        raise HTTPException(status_code=400, detail="Template desativado.")
    return {
        "id": template.id,
        "key": template.key,
        "version": template.version,
        "body": template.body,
    }


@app.post("/templates/{template_id}/deactivate")
def deactivate_template(
    template_id: int, session: Session = Depends(get_session)
) -> Response:
    template = session.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template nao encontrado.")
    if template.is_active:
        template.is_active = False
        session.add(template)
        session.commit()
    return RedirectResponse(url="/templates", status_code=303)


@app.post("/templates/{template_id}/activate")
def activate_template(
    template_id: int, session: Session = Depends(get_session)
) -> Response:
    template = session.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template nao encontrado.")
    if not template.is_active:
        others = session.exec(
            select(Template).where(Template.key == template.key, Template.id != template.id)
        ).all()
        for other in others:
            other.is_active = False
            session.add(other)
        template.is_active = True
        session.add(template)
        session.commit()
    return RedirectResponse(url="/templates", status_code=303)


@app.post("/templates/{template_id}/update", response_class=HTMLResponse)
def update_template(
    template_id: int,
    template: str = Form(...),
    data: str = Form("{}"),
    template_key: str = Form(""),
    template_version: str = Form(""),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    existing = session.get(Template, template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Template nao encontrado.")

    template_error = validate_template_text(template)
    if template_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=template_error,
                template_key=template_key,
                template_version=template_version,
                template_id=str(template_id),
            ),
            status_code=400,
        )

    normalized_key, key_error = normalize_template_key(template_key)
    if key_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=key_error,
                template_key=template_key,
                template_version=template_version,
                template_id=str(template_id),
            ),
            status_code=400,
        )

    version, version_error = parse_template_version(template_version)
    if version_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=version_error,
                template_key=normalized_key or template_key,
                template_version=template_version,
                template_id=str(template_id),
            ),
            status_code=400,
        )

    duplicate = session.exec(
        select(Template).where(
            Template.key == normalized_key,
            Template.version == version,
            Template.id != template_id,
        )
    ).first()
    if duplicate:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error="Ja existe um template com esse nome e versao.",
                template_key=normalized_key,
                template_version=str(version),
                template_id=str(template_id),
            ),
            status_code=400,
        )

    existing.key = normalized_key
    existing.version = version
    existing.body = template
    existing.updated_at = datetime.utcnow()
    session.add(existing)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error="Ja existe um template com esse nome e versao.",
                template_key=normalized_key,
                template_version=str(version),
                template_id=str(template_id),
            ),
            status_code=400,
        )
    except Exception as exc:
        session.rollback()
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=f"Erro ao atualizar: {exc}",
                template_key=normalized_key,
                template_version=str(version),
                template_id=str(template_id),
            ),
            status_code=500,
        )

    return HTMLResponse(
        render_page_with_templates(
            session,
            template,
            data,
            notice="Template atualizado com sucesso.",
            template_key=normalized_key,
            template_version=str(version),
            template_id=str(template_id),
        )
    )


@app.post("/generate", response_class=HTMLResponse)
def generate(
    template: str = Form(...),
    data: str = Form("{}"),
    template_key: str = Form(""),
    template_version: str = Form(""),
    template_id: str = Form(""),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    template_text, template_record, template_error = resolve_template_for_form(
        session, template, template_id
    )
    if template_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=template_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    data_obj, error = parse_form_data(data)
    if error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    render_data, custom_error = prepare_custom_pages_data(data_obj)
    if custom_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=custom_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    output_html, render_error = render_html_safe(
        template_text or template, render_data or data_obj
    )
    if render_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=render_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    save_error = save_report(
        session, template_text or template, data_obj, output_html, template_record
    )
    notice = None
    if save_error:
        notice = f"Relatorio gerado, mas nao foi possivel salvar. {save_error}"

    return HTMLResponse(
        render_page_with_templates(
            session,
            template_text or template,
            data,
            output=output_html,
            notice=notice,
            template_key=template_key,
            template_version=template_version,
            template_id=template_id,
        )
    )


@app.post("/download")
def download(
    template: str = Form(...),
    data: str = Form("{}"),
    template_key: str = Form(""),
    template_version: str = Form(""),
    template_id: str = Form(""),
    session: Session = Depends(get_session),
) -> Response:
    template_text, template_record, template_error = resolve_template_for_form(
        session, template, template_id
    )
    if template_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=template_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    data_obj, error = parse_form_data(data)
    if error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    render_data, custom_error = prepare_custom_pages_data(data_obj)
    if custom_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=custom_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    output_html, render_error = render_html_safe(
        template_text or template, render_data or data_obj
    )
    if render_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=render_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    save_report(
        session, template_text or template, data_obj, output_html, template_record
    )
    headers = {"Content-Disposition": 'attachment; filename="relatorio.html"'}
    return Response(
        content=output_html,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


@app.post("/download/pdf")
def download_pdf(
    request: Request,
    template: str = Form(...),
    data: str = Form("{}"),
    template_key: str = Form(""),
    template_version: str = Form(""),
    template_id: str = Form(""),
    session: Session = Depends(get_session),
) -> Response:
    template_text, template_record, template_error = resolve_template_for_form(
        session, template, template_id
    )
    if template_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=template_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    data_obj, error = parse_form_data(data)
    if error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    render_data, custom_error = prepare_custom_pages_data(data_obj)
    if custom_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=custom_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    output_html, render_error = render_html_safe(
        template_text or template, render_data or data_obj
    )
    if render_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template_text or template,
                data,
                error=render_error,
                template_key=template_key,
                template_version=template_version,
                template_id=template_id,
            ),
            status_code=400,
        )

    save_report(
        session, template_text or template, data_obj, output_html, template_record
    )
    title = template_key or "Relatorio"
    pdf_bytes = render_pdf_bytes(
        output_html, base_url=str(request.base_url), title=title
    )
    headers = {"Content-Disposition": 'attachment; filename="relatorio.pdf"'}
    return Response(
        content=pdf_bytes, media_type="application/pdf", headers=headers
    )


@app.post("/templates/save", response_class=HTMLResponse)
def save_template(
    template: str = Form(...),
    data: str = Form("{}"),
    template_key: str = Form(""),
    template_version: str = Form(""),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    template_error = validate_template_text(template)
    if template_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=template_error,
                template_key=template_key,
                template_version=template_version,
            ),
            status_code=400,
        )

    normalized_key, key_error = normalize_template_key(template_key)
    if key_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=key_error,
                template_key=template_key,
                template_version=template_version,
            ),
            status_code=400,
        )

    version, version_error = parse_template_version(template_version)
    if version_error:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=version_error,
                template_key=normalized_key or template_key,
                template_version=template_version,
            ),
            status_code=400,
        )

    existing = session.exec(
        select(Template).where(
            Template.key == normalized_key, Template.version == version
        )
    ).first()
    if existing:
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error="Ja existe um template com esse nome e versao.",
                template_key=normalized_key,
                template_version=str(version),
            ),
            status_code=400,
        )

    new_template = Template(
        key=normalized_key,
        version=version,
        body=template,
    )
    session.add(new_template)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error="Ja existe um template com esse nome e versao.",
                template_key=normalized_key,
                template_version=str(version),
            ),
            status_code=400,
        )
    except Exception as exc:
        session.rollback()
        return HTMLResponse(
            render_page_with_templates(
                session,
                template,
                data,
                error=f"Erro ao salvar: {exc}",
                template_key=normalized_key,
                template_version=str(version),
            ),
            status_code=500,
        )

    return HTMLResponse(
        render_page_with_templates(
            session,
            template,
            data,
            notice=f"Template salvo com sucesso (v{version}).",
            template_key=normalized_key,
            template_version=str(version),
        )
    )


@app.post("/api/render")
def render_api(
    payload: RenderRequest, session: Session = Depends(get_session)
) -> dict[str, str]:
    template_text, template_record, template_error = resolve_template_for_payload(
        session, payload
    )
    if template_error:
        raise HTTPException(status_code=400, detail=template_error)

    validated_data, data_error = validate_data_obj(payload.data)
    if data_error:
        raise HTTPException(status_code=400, detail=data_error)

    render_data, custom_error = prepare_custom_pages_data(validated_data)
    if custom_error:
        raise HTTPException(status_code=400, detail=custom_error)

    output_html, render_error = render_html_safe(
        template_text or "", render_data or validated_data
    )
    if render_error:
        raise HTTPException(status_code=400, detail=render_error)

    save_report(session, template_text or "", validated_data, output_html, template_record)
    return {"html": output_html}


@app.post("/api/html/preview")
def html_preview(payload: HtmlPreviewRequest) -> dict[str, str]:
    output_html = payload.html or ""
    if len(output_html) > MAX_OUTPUT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"HTML muito longo (max {MAX_OUTPUT_CHARS} caracteres).",
        )
    return {"html": render_html_preview(output_html)}


@app.post("/api/pdf")
def html_pdf(payload: HtmlPdfRequest, request: Request) -> Response:
    output_html = payload.html or ""
    if len(output_html) > MAX_OUTPUT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"HTML muito longo (max {MAX_OUTPUT_CHARS} caracteres).",
        )
    title = payload.title or "Relatorio"
    pdf_bytes = render_pdf_bytes(
        output_html, base_url=str(request.base_url), title=title
    )
    headers = {"Content-Disposition": 'attachment; filename="relatorio.pdf"'}
    return Response(
        content=pdf_bytes, media_type="application/pdf", headers=headers
    )


@app.get("/private/logo")
def get_private_logo(
    token: str | None = None, x_logo_token: str | None = Header(None)
) -> Response:
    if not PRIVATE_LOGO_PATH:
        raise HTTPException(
            status_code=404, detail="Logo privado nao configurado."
        )

    if PRIVATE_LOGO_REQUIRE_TOKEN:
        if not PRIVATE_LOGO_TOKEN:
            raise HTTPException(
                status_code=404, detail="Logo privado nao configurado."
            )
        provided = token or x_logo_token
        if not provided or not secrets.compare_digest(
            provided, PRIVATE_LOGO_TOKEN
        ):
            raise HTTPException(status_code=403, detail="Token invalido.")

    path = Path(PRIVATE_LOGO_PATH)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Logo nao encontrado.")

    media_type = PRIVATE_LOGO_MEDIA_TYPE or mimetypes.guess_type(
        path.name
    )[0]
    if not media_type:
        media_type = "application/octet-stream"

    headers = {
        "Cache-Control": f"private, max-age={PRIVATE_LOGO_CACHE_SECONDS}"
    }
    return FileResponse(path, media_type=media_type, headers=headers)


@app.post("/private/logo/upload")
async def upload_private_logo(
    file: UploadFile = File(...),
    token: str | None = None,
    x_logo_token: str | None = Header(None),
) -> dict[str, str]:
    if not PRIVATE_LOGO_PATH:
        raise HTTPException(
            status_code=404, detail="Logo privado nao configurado."
        )

    provided = token or x_logo_token
    if PRIVATE_LOGO_REQUIRE_TOKEN:
        if not PRIVATE_LOGO_TOKEN:
            raise HTTPException(
                status_code=404, detail="Logo privado nao configurado."
            )
        if not provided or not secrets.compare_digest(
            provided, PRIVATE_LOGO_TOKEN
        ):
            raise HTTPException(status_code=403, detail="Token invalido.")

    content_type = (file.content_type or "").lower()
    guessed_type, _ = mimetypes.guess_type(file.filename or "")
    allowed_types = {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/svg+xml",
    }
    if content_type not in allowed_types and guessed_type not in allowed_types:
        raise HTTPException(
            status_code=400, detail="Formato de logo nao permitido."
        )

    data = await read_upload_file_limited_generic(
        file, MAX_LOGO_BYTES, "Logo"
    )
    path = Path(PRIVATE_LOGO_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    if provided:
        logo_url = f"/private/logo?token={provided}"
    else:
        logo_url = "/private/logo"
    return {"logo_url": logo_url}


@app.post("/api/csv/extract")
async def extract_csv(
    file: UploadFile | None = File(None),
    delimiter: str | None = Form(None),
    has_header: bool = Form(True),
    limit: int | None = Form(None),
    template: str | None = Form(None),
) -> Response:
    file_name = file.filename if file else "exemplo.csv"
    if file is None:
        text = CSV_EXAMPLE_TEXT
    else:
        raw = await read_upload_file_limited(file, MAX_CSV_BYTES)
        text = decode_csv_bytes(raw)
    delimiter_value = delimiter.strip() if delimiter else None
    if delimiter_value is None and file is None:
        delimiter_value = ";"
    if delimiter_value:
        lowered = delimiter_value.lower()
        if lowered in ("\\t", "tab"):
            delimiter_value = "\t"
    headers, parsed_rows, truncated, used_delimiter, total_rows = parse_csv_text(
        text, delimiter_value, has_header
    )
    limit_value = clamp_csv_limit(limit)
    rows_out = parsed_rows[:limit_value]
    sampled = len(parsed_rows) > len(rows_out) or truncated
    response: dict[str, Any] = {
        "file_name": file_name,
        "headers": headers,
        "rows": rows_out,
        "rows_returned": len(rows_out),
        "total_rows": min(total_rows, MAX_CSV_ROWS),
        "sampled": sampled,
        "truncated": truncated,
        "delimiter": used_delimiter,
        "has_header": has_header,
    }
    template_value = template if template and template.strip() else CSV_EXAMPLE_TEMPLATE
    template_error = validate_template_text(template_value)
    if template_error:
        raise HTTPException(status_code=400, detail=template_error)
    output_html, render_error = render_html_safe(template_value, response)
    if render_error:
        raise HTTPException(status_code=400, detail=render_error)
    filename = "csv_relatorio.html"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=output_html,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


@app.post("/api/csv/llm")
async def render_csv_with_llm(
    file: UploadFile = File(...),
    delimiter: str | None = Form(None),
    has_header: bool = Form(True),
    title: str | None = Form(None),
    description: str | None = Form(None),
    model: str | None = Form(None),
    base_url: str | None = Form(None),
    temperature: float | None = Form(None),
) -> Response:
    raw = await read_upload_file_limited(file, MAX_CSV_BYTES)
    text = decode_csv_bytes(raw)
    delimiter_value = delimiter.strip() if delimiter else None
    if delimiter_value:
        lowered = delimiter_value.lower()
        if lowered in ("\\t", "tab"):
            delimiter_value = "\t"

    title_hint = title.strip() if title else None
    description_hint = description.strip() if description else None
    table_html, _meta = await generate_llm_html_from_csv(
        text,
        delimiter_value,
        has_header,
        title_hint,
        description_hint,
        model,
        base_url,
        temperature,
        include_header=False,
    )
    filename = "csv_relatorio_llm.html"
    response_headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=table_html,
        media_type="text/html; charset=utf-8",
        headers=response_headers,
    )


@app.post("/api/render_with_tables")
async def render_with_tables(
    payload: RenderWithTablesRequest, session: Session = Depends(get_session)
) -> dict[str, str]:
    report_style = payload.report_style or "default"
    template_input = payload.template.strip() if payload.template else ""
    if report_style == "hoftalon" and template_input == FLOW_TEMPLATE_EXAMPLE.strip():
        template_input = ""
    template_request = RenderRequest(
        template=template_input or None,
        template_id=payload.template_id,
        template_key=payload.template_key,
        template_version=payload.template_version,
        data=payload.data,
    )
    template_text = None
    template_record = None
    if report_style == "hoftalon":
        if template_input or payload.template_id or payload.template_key:
            template_text, template_record, template_error = resolve_template_for_payload(
                session, template_request
            )
            if template_error:
                raise HTTPException(status_code=400, detail=template_error)
        else:
            template_text = HOFTALON_BASE_TEMPLATE
        template_error = validate_template_text(template_text or "")
        if template_error:
            raise HTTPException(status_code=400, detail=template_error)
    else:
        template_text, template_record, template_error = resolve_template_for_payload(
            session, template_request
        )
        if template_error:
            raise HTTPException(status_code=400, detail=template_error)

    data_obj, data_error = validate_data_obj(payload.data)
    if data_error:
        raise HTTPException(status_code=400, detail=data_error)
    if report_style == "hoftalon":
        data_error = validate_hoftalon_data(data_obj)
        if data_error:
            raise HTTPException(status_code=400, detail=data_error)

    if len(payload.tables) > MAX_LLM_TABLES:
        raise HTTPException(
            status_code=400,
            detail=f"Numero maximo de tabelas (max {MAX_LLM_TABLES}).",
        )

    tables_html: dict[str, str] = {}
    tables_meta: list[dict[str, Any]] = []
    for table in payload.tables:
        key, key_error = normalize_table_key(table.key)
        if key_error:
            raise HTTPException(status_code=400, detail=key_error)
        if key in tables_html:
            raise HTTPException(
                status_code=400, detail=f"Tabela duplicada: {key}."
            )
        csv_text = table.csv.strip()
        if not csv_text:
            raise HTTPException(
                status_code=400, detail=f"CSV vazio para a tabela {key}."
            )
        csv_bytes = csv_text.encode("utf-8")
        if len(csv_bytes) > MAX_CSV_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"CSV muito grande para a tabela {key}.",
            )
        delimiter_value = table.delimiter.strip() if table.delimiter else None
        if delimiter_value:
            lowered = delimiter_value.lower()
            if lowered in ("\\t", "tab"):
                delimiter_value = "\t"

        title_hint = table.title.strip() if table.title else None
        description_hint = table.description.strip() if table.description else None
        if report_style == "hoftalon" and key == "atividades":
            table_html, meta = build_hoftalon_activities_table(
                csv_text, delimiter_value, table.has_header
            )
        else:
            table_html, meta = await generate_llm_html_from_csv(
                csv_text,
                delimiter_value,
                table.has_header,
                title_hint,
                description_hint,
                payload.model,
                payload.base_url,
                payload.temperature,
                include_header=False,
            )
        tables_html[key] = table_html
        tables_meta.append({"key": key, **meta})

    render_data = dict(data_obj)
    if tables_html:
        render_data["tables_html"] = tables_html
        render_data["tables_meta"] = tables_meta
    if report_style == "hoftalon":
        render_data = build_hoftalon_render_data(render_data, tables_meta)
        render_data, custom_error = prepare_custom_pages_data(render_data)
        if custom_error:
            raise HTTPException(status_code=400, detail=custom_error)

    if report_style != "hoftalon":
        render_data, custom_error = prepare_custom_pages_data(render_data)
        if custom_error:
            raise HTTPException(status_code=400, detail=custom_error)

    output_html, render_error = render_html_safe(
        template_text or "", render_data
    )
    if render_error:
        raise HTTPException(status_code=400, detail=render_error)

    if report_style == "hoftalon":
        append_tables = False
    else:
        append_tables = payload.append_tables
        if append_tables is None:
            append_tables = "tables_html" not in (template_text or "")
    if append_tables and tables_html:
        output_html = output_html.rstrip() + "\n" + "\n".join(tables_html.values()) + "\n"

    if report_style == "hoftalon":
        output_error = validate_hoftalon_output(output_html, tables_meta)
        if output_error:
            raise HTTPException(status_code=400, detail=output_error)

    save_report(session, template_text or "", data_obj, output_html, template_record)
    return {"html": output_html}


def parse_form_data(data: str) -> tuple[dict[str, Any] | None, str | None]:
    data_error = validate_data_text(data)
    if data_error:
        return None, data_error

    try:
        data_obj = json.loads(data) if data.strip() else {}
    except json.JSONDecodeError as exc:
        return None, f"JSON invalido: {exc.msg}"

    validated_data, data_error = validate_data_obj(data_obj)
    if data_error:
        return None, data_error

    return validated_data, None
