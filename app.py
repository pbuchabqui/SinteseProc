"""
app.py — Síntese de Decisões Trabalhista
IA usada para critérios de liquidação e alertas periciais.
Extrações determinísticas ficam em módulos testáveis.
"""

import os

import streamlit as st

from sintese.ai import ext_alertas_periciais, ext_criterios, montar_contexto_criterios, montar_contexto_longo
from sintese.exporters import gerar_excel, gerar_markdown, gerar_word
from sintese.extraction import (
    aplicar_ocr_necessario,
    analisar_pdf_texto,
    buscar_secoes,
    extrair_dados,
    extrair_decisoes,
    extrair_ficha,
    extrair_ponto,
    montar_estrutura_pdf,
    ler_pdf,
)

# ── Config ────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Síntese de Decisões", page_icon="⚖️")
st.title("⚖️ Síntese de Decisões Trabalhista")
st.caption("Upload PDF → extrair → baixar Word / Markdown / Excel")


def get_secret(k):
    try:
        return st.secrets[k]
    except Exception:
        return os.getenv(k)


GROQ_KEY = get_secret("GROQ_API_KEY")
if not GROQ_KEY:
    st.error("GROQ_API_KEY não configurada. Adicione em Settings → Secrets.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# BLOCO 4 — INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

arq = st.file_uploader("📄 PDF do processo", type=["pdf"])
if not arq:
    st.info("Faça o upload do PDF para continuar.")
    st.stop()
st.success(f"{arq.name} ({arq.size/1024/1024:.1f} MB)")

st.subheader("O que extrair?")
c1, c2 = st.columns(2)
with c1:
    f_dados     = st.checkbox("Dados do processo e partes",        value=True)
    f_decisoes  = st.checkbox("Decisões judiciais (dispositivos)", value=True)
    f_criterios = st.checkbox("Critérios de liquidação ⚡IA",      value=True)
    f_alertas   = st.checkbox("Alertas periciais ⚡IA",            value=True,
                               help="Seção 8: riscos, prazos, documentação faltante, PJe-Calc")
with c2:
    f_ficha = st.checkbox("Ficha financeira (holerites)")
    f_ponto = st.checkbox("Espelho de ponto")
st.caption("⚡IA = chamada ao Groq. Todo o resto é processamento local, sem custo.")

st.subheader("Arquivos de saída")
c3, c4 = st.columns(2)
with c3:
    o_word = st.checkbox("Word (.docx)",  value=True)
    o_md   = st.checkbox("Markdown (.md)")
with c4:
    o_xl   = st.checkbox("Excel (.xlsx)")
    if o_xl:
        st.caption("Abas Excel:")
        st.checkbox("LIQUIDACAO (sempre incluída)", value=True, disabled=True)
        aba_pag = st.checkbox("PAGAMENTOS (ficha)", disabled=not f_ficha,
                              help="Marque 'Ficha financeira' acima")
        aba_pto = st.checkbox("PONTO (espelho)",    disabled=not f_ponto,
                              help="Marque 'Espelho de ponto' acima")
    else:
        aba_pag = aba_pto = False

if not (o_word or o_md or o_xl):
    st.warning("Selecione pelo menos um arquivo de saída.")
    st.stop()

if not st.button("⚙️ Processar", type="primary"):
    st.stop()

# ── Processamento ─────────────────────────────────────────────────────────────

pdf_bytes = arq.read()
dados = decisoes_lista = criterios = ficha = ponto = alertas = None

with st.spinner("Lendo PDF..."):
    doc_fitz, capa, txt, toc, npags = ler_pdf(pdf_bytes)
    analise_pdf = analisar_pdf_texto(doc_fitz, nome_arquivo=arq.name, tamanho_bytes=arq.size)
    textos_paginas = list(analise_pdf.get("textos_paginas") or [])

st.write(f"✅ {npags} páginas lidas")
confianca_global = analise_pdf.get("classificacao_tecnica", {}).get("confianca_global")
if confianca_global:
    st.caption(f"Classificação técnica: {analise_pdf['tipo_pdf']} — confiança {confianca_global}.")

if analise_pdf["total_precisam_ocr"]:
    paginas_ocr = ", ".join(map(str, analise_pdf["paginas_precisam_ocr"][:20]))
    extra = "..." if len(analise_pdf["paginas_precisam_ocr"]) > 20 else ""
    st.warning(
        f"🔎 Análise inicial: {analise_pdf['total_precisam_ocr']}/{npags} página(s) precisam de OCR "
        f"(páginas {paginas_ocr}{extra})."
    )
    with st.spinner("Executando OCR nas páginas necessárias..."):
        ocr = aplicar_ocr_necessario(doc_fitz, analise_pdf, pdf_bytes=pdf_bytes)
    if ocr["paginas_processadas"]:
        capa = ocr["capa"]
        txt = ocr["texto_completo"]
        textos_paginas = ocr["textos_paginas"]
        estrutura_pdf = ocr.get("estrutura_pdf") or montar_estrutura_pdf(analise_pdf, textos_paginas)
        engine = ocr.get("engine") or "ocr"
        st.write(f"✅ OCR concluído em {len(ocr['paginas_processadas'])} página(s) — motor: {engine}")
    if ocr["erros"]:
        paginas_erro = ", ".join(map(str, sorted(ocr["erros"])[:10]))
        extra_erro = "..." if len(ocr["erros"]) > 10 else ""
        st.warning(
            f"⚠️ OCR não concluiu em {len(ocr['erros'])} página(s): {paginas_erro}{extra_erro}. "
            "Verifique se o Tesseract e o idioma português estão disponíveis."
        )
else:
    estrutura_pdf = montar_estrutura_pdf(analise_pdf, textos_paginas)
    st.write(
        f"🔎 Análise inicial: texto nativo aproveitável em "
        f"{analise_pdf['total_nativas']}/{npags} página(s) ({analise_pdf['percentual_nativo']}%)."
    )

if "estrutura_pdf" not in locals():
    estrutura_pdf = montar_estrutura_pdf(analise_pdf, textos_paginas)

if analise_pdf["total_baixo_texto"] or analise_pdf["total_sem_texto"]:
    st.caption(
        f"Páginas com baixo texto: {analise_pdf['total_baixo_texto']} — "
        f"sem texto detectável: {analise_pdf['total_sem_texto']}."
    )
for alerta_pre in estrutura_pdf.get("alertas", []):
    st.warning(alerta_pre)

secs = buscar_secoes(doc_fitz, toc, txt, textos_paginas=textos_paginas)
secoes_ok = list(secs.keys())
st.write(
    "✅ Seções localizadas: " + ", ".join(secoes_ok)
    if secoes_ok
    else "⚠️ Nenhuma seção localizada por palavra-chave"
)

if f_dados:
    with st.spinner("Extraindo dados do processo (regex)..."):
        dados = extrair_dados(txt, capa)
    num = dados.get("numero_processo","?")
    rec = dados.get("reclamante","?")
    emp = dados.get("reclamada_1","?")
    st.write(f"✅ Processo {num} — {rec} × {emp}")

if f_decisoes:
    with st.spinner("Extraindo decisões (texto literal)..."):
        decisoes_lista = extrair_decisoes(secs)
    n = len(decisoes_lista)
    if n:
        tipos = ", ".join(d["tipo"] for d in decisoes_lista)
        st.write(f"✅ {n} decisão(ões): {tipos}")
    else:
        st.write("⚠️ Nenhuma decisão localizada")

if f_criterios:
    decisoes_para_criterios = decisoes_lista
    if decisoes_para_criterios is None:
        decisoes_para_criterios = extrair_decisoes(secs)
    contexto_criterios = montar_contexto_criterios(
        decisoes_para_criterios or [],
        secs,
        txt,
        estrutura_pdf=estrutura_pdf,
    )
    with st.spinner("Extraindo critérios de liquidação (⚡IA — 1 chamada)..."):
        criterios = ext_criterios(contexto_criterios, get_secret, GROQ_KEY, st.warning)
    if "erro" in criterios:
        st.error(f"❌ Critérios: {criterios['erro']}")
    else:
        st.write("✅ Critérios extraídos")

if f_alertas:
    decisoes_para_alertas = decisoes_lista
    if decisoes_para_alertas is None:
        decisoes_para_alertas = extrair_decisoes(secs)
    texto_alertas = montar_contexto_longo(
        decisoes_para_alertas or [],
        secs,
        txt,
        estrutura_pdf=estrutura_pdf,
        objetivo="alertas",
        limite=20000,
    )
    with st.spinner("Gerando alertas periciais (⚡IA — 1 chamada)..."):
        alertas = ext_alertas_periciais(texto_alertas, get_secret, GROQ_KEY, st.warning)
    if "erro" in alertas:
        st.error(f"❌ Alertas: {alertas['erro']}")
    else:
        n_alertas = len(alertas.get("alertas", {}).get("pontos_atencao", []))
        st.write(f"✅ Alertas gerados ({n_alertas} ponto(s) de atenção)")

if f_ficha:
    with st.spinner("Extraindo ficha financeira (tabelas PyMuPDF)..."):
        ficha = extrair_ficha(doc_fitz, textos_paginas=textos_paginas)
    if "erro" in ficha:
        st.warning(f"⚠️ Ficha: {ficha['erro']}")
    else:
        n = len(ficha.get("competencias",[]))
        st.write(f"✅ Ficha: {n} competência(s), {len(ficha.get('rubricas',[]))} rubrica(s)")

if f_ponto:
    with st.spinner("Extraindo espelho de ponto (tabelas PyMuPDF)..."):
        ponto = extrair_ponto(doc_fitz, textos_paginas=textos_paginas)
    if "erro" in ponto:
        st.warning(f"⚠️ Ponto: {ponto['erro']}")
    else:
        st.write(f"✅ Ponto: {len(ponto.get('registros',[]))} registro(s)")

doc_fitz.close()

# ── Downloads ─────────────────────────────────────────────────────────────────

st.success("✅ Concluído!")
st.subheader("📥 Downloads")
num = (dados or {}).get("numero_processo","processo").replace("-","").replace(".","")

if o_word:
    with st.spinner("Gerando Word..."):
        wb = gerar_word(dados or {}, decisoes_lista or [], criterios or {}, alertas or {})
    st.download_button("📄 Word (.docx)", wb, f"sintese_{num}.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

if o_md:
    with st.spinner("Gerando Markdown..."):
        md = gerar_markdown(dados or {}, decisoes_lista or [], criterios or {}, alertas or {})
    st.download_button("📝 Markdown (.md)", md.encode(), f"sintese_{num}.md","text/markdown")

if o_xl:
    with st.spinner("Gerando Excel..."):
        xl = gerar_excel(dados or {}, ficha or {}, ponto or {}, aba_pag, aba_pto)
    st.download_button("📊 Excel (.xlsx)", xl, f"liquidacao_{num}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
