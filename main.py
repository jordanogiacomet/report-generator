from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from datetime import date, datetime
import html
import json
from math import ceil
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

try:
    from db import init_db, get_session
    from models import Report, Template
except ModuleNotFoundError:
    from .db import init_db, get_session
    from .models import Report, Template


DEFAULT_TEMPLATE = """# Relatorio para {{ client }}

Data: {{ date }}

## Itens
{% for item in items %}
- {{ item.name }}: {{ item.qty }}
{% endfor %}
"""

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
TUTORIAL_TEMPLATE_EXAMPLE = "# Relatorio para {{ client }}"
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
MAX_TEMPLATE_CHARS = 10000
MAX_TEMPLATE_KEY_CHARS = 80
MAX_DATA_CHARS = 20000
MAX_OUTPUT_CHARS = 50000
MAX_RENDER_SECONDS = 2.0
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
  align-items: flex-end;
  justify-content: space-between;
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

.hero-card {
  background: linear-gradient(135deg, rgba(15, 118, 110, 0.12), rgba(217, 119, 6, 0.12));
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px 16px;
  min-width: 220px;
  box-shadow: var(--shadow);
}

.hero-card p {
  margin: 0 0 8px 0;
  font-family: "Space Grotesk", "Avenir Next", sans-serif;
  font-weight: 600;
}

.hero-card ul {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 0.9rem;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
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
  border-radius: 10px;
  border: 1px solid var(--line);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.12);
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
}

.markdown-preview table {
  width: 100%;
  border-collapse: collapse;
  margin: 0;
  font-size: 0.95rem;
  min-width: 520px;
}

.markdown-preview th,
.markdown-preview td {
  border: 1px solid var(--line);
  padding: 8px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
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
  .grid {
    grid-template-columns: 1fr;
  }
  .hero {
    align-items: flex-start;
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


jinja_env = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
render_executor = ThreadPoolExecutor(max_workers=4)
markdown_renderer = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(
    ["table", "strikethrough"]
)
try:
    from mdit_py_plugins.deflist import deflist
    from mdit_py_plugins.footnote import footnote
    from mdit_py_plugins.tasklists import tasklists

    markdown_renderer.use(tasklists, enabled=True)
    markdown_renderer.use(footnote)
    markdown_renderer.use(deflist)
except ModuleNotFoundError:
    pass


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


def render_markdown(template_text: str, data: dict[str, Any]) -> str:
    template = jinja_env.from_string(template_text)
    return template.render(**data)


def render_markdown_safe(
    template_text: str, data: dict[str, Any]
) -> tuple[str | None, str | None]:
    try:
        future = render_executor.submit(render_markdown, template_text, data)
        markdown = future.result(timeout=MAX_RENDER_SECONDS)
    except FutureTimeoutError:
        return None, f"Tempo limite de render (max {MAX_RENDER_SECONDS}s)."
    except Exception as exc:
        return None, f"Erro no template: {exc}"

    if len(markdown) > MAX_OUTPUT_CHARS:
        return None, f"Saida muito longa (max {MAX_OUTPUT_CHARS} caracteres)."

    return markdown, None


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


def save_report(
    session: Session,
    template: str,
    data_obj: dict[str, Any],
    markdown: str,
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
        markdown=markdown,
    )
    session.add(report)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        return f"Erro ao salvar relatorio: {exc}"
    return None


def fetch_active_templates(session: Session) -> list[Template]:
    return session.exec(
        select(Template)
        .where(Template.is_active == True)
        .order_by(Template.key, Template.version.desc())
    ).all()


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
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "..."


def render_report_preview(body: str, limit: int = 260) -> str:
    return render_template_preview(body, limit=limit)


def render_markdown_preview(markdown: str) -> str:
    rendered = markdown_renderer.render(markdown)
    rendered = re.sub(r'href="javascript:[^"]*"', 'href="#"', rendered, flags=re.IGNORECASE)
    rendered = re.sub(r'src="javascript:[^"]*"', 'src=""', rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"<table>", '<div class="table-wrap"><table>', rendered)
    rendered = re.sub(r"</table>", "</table></div>", rendered)
    return rendered


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
        output_rendered = render_markdown_preview(output)
        output_html = f"""
      <section class="card output-card">
        <div class="card-header">
          <h2>Saida</h2>
          <span class="badge accent">Markdown</span>
          <button type="button" class="btn ghost copy-btn" data-copy-target="#markdown-raw">
            Copiar
          </button>
          <span class="copy-status" id="copy-status" aria-live="polite"></span>
        </div>
        <div class="markdown-preview">{output_rendered}</div>
        <details class="raw-output">
          <summary>Ver Markdown bruto</summary>
          <pre class="code-block" id="markdown-raw">{output_escaped}</pre>
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
    tutorial_placeholder = html.escape(TUTORIAL_PLACEHOLDER)
    tutorial_json_inline = html.escape(TUTORIAL_JSON_INLINE)
    tutorial_template_example = html.escape(TUTORIAL_TEMPLATE_EXAMPLE)
    tutorial_json_example = html.escape(TUTORIAL_JSON_EXAMPLE)
    tutorial_schema_hint = html.escape(TUTORIAL_SCHEMA_HINT)
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
            "body": template.body,
        }
        for template in templates_list
    }
    templates_json = json.dumps(templates_payload, ensure_ascii=True).replace("<", "\\u003c")

    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Gerador de Markdown</title>
    <style>
{BASE_CSS}
    </style>
  </head>
  <body>
    <main class="shell">
      {nav_html}
      <header class="hero">
        <div>
          <p class="eyebrow">MVP</p>
          <h1>Gerador de Markdown</h1>
          <p class="lead">Escreva um template Jinja2 e dados JSON, depois gere o Markdown.</p>
        </div>
        <div class="hero-card">
          <p>Limites atuais</p>
          <ul>
            <li>Template: {MAX_TEMPLATE_CHARS} chars</li>
            <li>JSON: {MAX_DATA_CHARS} chars</li>
            <li>Saida: {MAX_OUTPUT_CHARS} chars</li>
            <li>Render: {MAX_RENDER_SECONDS}s</li>
          </ul>
        </div>
      </header>
      <section class="grid">
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
              <button type="submit" class="btn secondary" formaction="/download">Baixar</button>
              <button type="submit" class="btn ghost" formaction="/templates/save">Salvar template</button>
              {update_button_html}
            </div>
          </form>
        </section>
        <section class="card">
          <h2>Tutorial rapido</h2>
          <ol class="steps">
            <li>Edite o template usando placeholders do Jinja2 como <code>{tutorial_placeholder}</code>.</li>
            <li>Cole os dados JSON com as mesmas chaves, por exemplo <code>{tutorial_json_inline}</code>.</li>
            <li>Clique em <strong>Gerar</strong> para ver a saida ou em <strong>Baixar</strong> para baixar o arquivo.</li>
          </ol>
          <p class="hint">{tutorial_schema_hint}</p>
          <p>Exemplo de template:</p>
          <pre class="code-block">{tutorial_template_example}</pre>
          <p>Exemplo de JSON:</p>
          <pre class="code-block">{tutorial_json_example}</pre>
        </section>
      </section>
      {output_html}
    </main>
    <script type="application/json" id="template-data">{templates_json}</script>
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

        const applyTemplate = (templateId) => {{
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
          if (templateBodyInput) {{
            templateBodyInput.value = selected.body || "";
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
        <div class="hero-card">
          <p>Dicas rapidas</p>
          <ul>
            <li>Use "Abrir" para carregar no gerador</li>
            <li>Desative para organizar a lista</li>
          </ul>
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
            markdown_size = len(report.markdown)
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
              <div class="template-meta">T {template_size} - J {data_size} - M {markdown_size}</div>
            </div>
            <div class="actions">
              <a class="btn secondary" href="/reports/{report.id}">Abrir</a>
              <a class="btn ghost" href="/reports/{report.id}/download">Baixar</a>
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
        <div class="hero-card">
          <p>Atalhos</p>
          <ul>
            <li>Use a busca para localizar saidas antigas</li>
            <li>Abra para editar e gerar de novo</li>
          </ul>
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Report Generator",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
def read_root(session: Session = Depends(get_session)) -> str:
    return render_page_with_templates(session, DEFAULT_TEMPLATE, DEFAULT_DATA)


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
    filename = f"relatorio_{report.id}.md"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=report.markdown,
        media_type="text/markdown; charset=utf-8",
        headers=headers,
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

    markdown, render_error = render_markdown_safe(template_text or template, data_obj)
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
        session, template_text or template, data_obj, markdown, template_record
    )
    notice = None
    if save_error:
        notice = f"Relatorio gerado, mas nao foi possivel salvar. {save_error}"

    return HTMLResponse(
        render_page_with_templates(
            session,
            template_text or template,
            data,
            output=markdown,
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

    markdown, render_error = render_markdown_safe(template_text or template, data_obj)
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
        session, template_text or template, data_obj, markdown, template_record
    )
    headers = {"Content-Disposition": 'attachment; filename="relatorio.md"'}
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers=headers,
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

    markdown, render_error = render_markdown_safe(template_text or "", validated_data)
    if render_error:
        raise HTTPException(status_code=400, detail=render_error)

    save_report(session, template_text or "", validated_data, markdown, template_record)
    return {"markdown": markdown}


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
