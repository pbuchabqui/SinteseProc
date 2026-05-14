"""
app.py — Síntese de Decisões Trabalhista
IA usada para critérios de liquidação e alertas periciais.
Extrações determinísticas ficam em módulos testáveis.
"""

import os

import streamlit as st

from sintese.ai import (
    extrair_json_resposta_manual,
    ext_alertas_periciais,
    ext_criterios,
    montar_contexto_criterios,
    montar_contexto_longo,
    montar_prompt_manual_alertas,
    montar_prompt_manual_criterios,
    validar_resposta_manual,
)
from sintese.exporters import gerar_excel, gerar_json_bytes, gerar_markdown, gerar_word
from sintese.extraction import (
    aplicar_ocr_necessario,
    analisar_pdf_texto,
    anotar_decisoes_com_auditoria,
    avaliar_bloqueio_processamento,
    buscar_secoes,
    extrair_dados,
    extrair_decisoes,
    extrair_ficha,
    extrair_ponto,
    gerar_relatorio_preprocessamento_pdf,
    gerar_texto_sanitizado,
    montar_estrutura_pdf,
    validar_ficha,
    validar_ponto,
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


MODO_IA_GROQ = "Groq API"
MODO_IA_MANUAL = "Manual ChatGPT/Claude"
PROVEDOR_CHATGPT = "ChatGPT Pro"
PROVEDOR_CLAUDE = "Claude Pro"

GROQ_KEY = get_secret("GROQ_API_KEY")
if GROQ_KEY:
    modo_ia = st.radio("Modo IA", [MODO_IA_GROQ, MODO_IA_MANUAL], horizontal=True)
else:
    modo_ia = MODO_IA_MANUAL
    st.info("GROQ_API_KEY não configurada. O sistema seguirá em modo manual ChatGPT/Claude.")

provedor_manual = PROVEDOR_CHATGPT
if modo_ia == MODO_IA_MANUAL:
    provedor_manual = st.radio("Assistente manual", [PROVEDOR_CHATGPT, PROVEDOR_CLAUDE], horizontal=True)

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

processar_agora = st.button("⚙️ Processar", type="primary")
if processar_agora:
    st.session_state.pop("ultimo_resultado", None)
    st.session_state.pop("resposta_manual_criterios", None)
    st.session_state.pop("resposta_manual_alertas", None)

if not processar_agora and "ultimo_resultado" not in st.session_state:
    st.stop()

# ── Processamento ─────────────────────────────────────────────────────────────

if processar_agora:
    pdf_bytes = arq.read()
    dados = decisoes_lista = criterios = ficha = ponto = alertas = None
    prompts_manuais = {}

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

    seguranca_pdf = avaliar_bloqueio_processamento(estrutura_pdf)
    if seguranca_pdf["mensagem"]:
        if seguranca_pdf["bloquear"]:
            st.error(f"🚫 {seguranca_pdf['mensagem']}")
        else:
            st.warning(f"⚠️ {seguranca_pdf['mensagem']}")

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
            decisoes_lista = anotar_decisoes_com_auditoria(extrair_decisoes(secs), estrutura_pdf)
        n = len(decisoes_lista)
        if n:
            tipos = ", ".join(d["tipo"] for d in decisoes_lista)
            st.write(f"✅ {n} decisão(ões): {tipos}")
        else:
            st.write("⚠️ Nenhuma decisão localizada")

    if f_criterios:
        if seguranca_pdf["bloquear"]:
            st.error("❌ Critérios bloqueados: PDF com confiabilidade técnica crítica.")
        else:
            decisoes_para_criterios = decisoes_lista
            if decisoes_para_criterios is None:
                decisoes_para_criterios = anotar_decisoes_com_auditoria(extrair_decisoes(secs), estrutura_pdf)
            contexto_criterios = montar_contexto_criterios(
                decisoes_para_criterios or [],
                secs,
                txt,
                estrutura_pdf=estrutura_pdf,
            )
            if modo_ia == MODO_IA_MANUAL:
                prompts_manuais["criterios_chatgpt"] = montar_prompt_manual_criterios(contexto_criterios, PROVEDOR_CHATGPT)
                prompts_manuais["criterios_claude"] = montar_prompt_manual_criterios(contexto_criterios, PROVEDOR_CLAUDE)
                st.write("✅ Prompt manual de critérios gerado")
            else:
                with st.spinner("Extraindo critérios de liquidação (⚡IA — 1 chamada)..."):
                    criterios = ext_criterios(contexto_criterios, get_secret, GROQ_KEY, st.warning)
                if "erro" in criterios:
                    st.error(f"❌ Critérios: {criterios['erro']}")
                else:
                    st.write("✅ Critérios extraídos")

    if f_alertas:
        if seguranca_pdf["bloquear"]:
            st.error("❌ Alertas IA bloqueados: PDF com confiabilidade técnica crítica.")
        else:
            decisoes_para_alertas = decisoes_lista
            if decisoes_para_alertas is None:
                decisoes_para_alertas = anotar_decisoes_com_auditoria(extrair_decisoes(secs), estrutura_pdf)
            texto_alertas = montar_contexto_longo(
                decisoes_para_alertas or [],
                secs,
                txt,
                estrutura_pdf=estrutura_pdf,
                objetivo="alertas",
                limite=20000,
            )
            if modo_ia == MODO_IA_MANUAL:
                prompts_manuais["alertas_chatgpt"] = montar_prompt_manual_alertas(texto_alertas, PROVEDOR_CHATGPT)
                prompts_manuais["alertas_claude"] = montar_prompt_manual_alertas(texto_alertas, PROVEDOR_CLAUDE)
                st.write("✅ Prompt manual de alertas gerado")
            else:
                with st.spinner("Gerando alertas periciais (⚡IA — 1 chamada)..."):
                    alertas = ext_alertas_periciais(texto_alertas, get_secret, GROQ_KEY, st.warning)
                if "erro" in alertas:
                    st.error(f"❌ Alertas: {alertas['erro']}")
                else:
                    n_alertas = len(alertas.get("alertas", {}).get("pontos_atencao", []))
                    st.write(f"✅ Alertas gerados ({n_alertas} ponto(s) de atenção)")

    if f_ficha:
        if seguranca_pdf["bloquear"]:
            ficha = {"erro": "Extração tabular bloqueada por confiabilidade técnica crítica."}
            st.error(f"❌ Ficha: {ficha['erro']}")
        else:
            with st.spinner("Extraindo ficha financeira (tabelas locais)..."):
                ficha = extrair_ficha(doc_fitz, textos_paginas=textos_paginas, pdf_bytes=pdf_bytes)
            ficha["validacao"] = validar_ficha(ficha)
            if "erro" in ficha:
                st.warning(f"⚠️ Ficha: {ficha['erro']}")
            else:
                n = len(ficha.get("competencias",[]))
                st.write(f"✅ Ficha: {n} competência(s), {len(ficha.get('rubricas',[]))} rubrica(s)")
            for alerta_ficha in ficha.get("validacao", {}).get("alertas", []):
                st.warning(f"⚠️ Validação ficha: {alerta_ficha}")

    if f_ponto:
        if seguranca_pdf["bloquear"]:
            ponto = {"erro": "Extração tabular bloqueada por confiabilidade técnica crítica."}
            st.error(f"❌ Ponto: {ponto['erro']}")
        else:
            with st.spinner("Extraindo espelho de ponto (tabelas locais)..."):
                ponto = extrair_ponto(doc_fitz, textos_paginas=textos_paginas, pdf_bytes=pdf_bytes)
            ponto["validacao"] = validar_ponto(ponto)
            if "erro" in ponto:
                st.warning(f"⚠️ Ponto: {ponto['erro']}")
            else:
                st.write(f"✅ Ponto: {len(ponto.get('registros',[]))} registro(s)")
            for alerta_ponto in ponto.get("validacao", {}).get("alertas", []):
                st.warning(f"⚠️ Validação ponto: {alerta_ponto}")

    doc_fitz.close()
    st.session_state["ultimo_resultado"] = {
        "dados": dados,
        "decisoes_lista": decisoes_lista,
        "criterios": criterios,
        "ficha": ficha,
        "ponto": ponto,
        "alertas": alertas,
        "estrutura_pdf": estrutura_pdf,
        "textos_paginas": textos_paginas,
        "prompts_manuais": prompts_manuais,
        "modo_ia": modo_ia,
        "provedor_manual": provedor_manual,
    }
else:
    resultado = st.session_state["ultimo_resultado"]
    dados = resultado.get("dados")
    decisoes_lista = resultado.get("decisoes_lista")
    criterios = resultado.get("criterios")
    ficha = resultado.get("ficha")
    ponto = resultado.get("ponto")
    alertas = resultado.get("alertas")
    estrutura_pdf = resultado.get("estrutura_pdf")
    textos_paginas = resultado.get("textos_paginas") or []
    prompts_manuais = resultado.get("prompts_manuais") or {}
    modo_ia = resultado.get("modo_ia", modo_ia)
    provedor_manual = resultado.get("provedor_manual", provedor_manual)
    st.info("Usando o último processamento. Clique em Processar para reprocessar o PDF.")

num = (dados or {}).get("numero_processo","processo").replace("-","").replace(".","")

if prompts_manuais:
    st.subheader("🤝 IA manual")
    st.caption(
        "Copie o prompt para o ChatGPT/Claude no navegador oficial e cole abaixo a resposta JSON recebida."
    )

    def _receber_resposta_manual(tipo: str, titulo: str, valor_atual: dict | None) -> dict | None:
        chave_chatgpt = f"{tipo}_chatgpt"
        chave_claude = f"{tipo}_claude"
        chave_prompt = chave_claude if provedor_manual == PROVEDOR_CLAUDE else chave_chatgpt
        prompt = prompts_manuais.get(chave_prompt)
        if not prompt:
            return valor_atual

        with st.expander(f"Prompt manual — {titulo}", expanded=valor_atual is None):
            st.text_area(
                f"Prompt para copiar — {titulo} ({provedor_manual})",
                prompt,
                height=260,
                key=f"prompt_manual_{chave_prompt}",
            )
            c_prompt_1, c_prompt_2 = st.columns(2)
            with c_prompt_1:
                if prompts_manuais.get(chave_chatgpt):
                    st.download_button(
                        f"⬇️ Prompt {titulo} ChatGPT (.txt)",
                        prompts_manuais[chave_chatgpt].encode(),
                        f"prompt_{tipo}_chatgpt_{num}.txt",
                        "text/plain",
                    )
            with c_prompt_2:
                if prompts_manuais.get(chave_claude):
                    st.download_button(
                        f"⬇️ Prompt {titulo} Claude (.txt)",
                        prompts_manuais[chave_claude].encode(),
                        f"prompt_{tipo}_claude_{num}.txt",
                        "text/plain",
                    )

            resposta = st.text_area(
                f"Cole aqui a resposta JSON — {titulo}",
                height=180,
                key=f"resposta_manual_{tipo}",
            )
            if not resposta.strip():
                if valor_atual and "erro" not in valor_atual:
                    st.success(f"✅ {titulo}: resposta manual já validada")
                else:
                    st.warning(f"⚠️ {titulo}: aguardando resposta manual em JSON")
                return valor_atual

            parsed = extrair_json_resposta_manual(resposta)
            validacao = validar_resposta_manual(parsed, tipo)
            if not validacao["valido"]:
                st.error(f"❌ {titulo}: {validacao['erro']}")
                return valor_atual

            st.success(f"✅ {titulo}: resposta manual validada")
            st.session_state["ultimo_resultado"][tipo] = parsed
            return parsed

    if prompts_manuais.get("criterios_chatgpt") or prompts_manuais.get("criterios_claude"):
        criterios = _receber_resposta_manual("criterios", "Critérios de liquidação", criterios)
    if prompts_manuais.get("alertas_chatgpt") or prompts_manuais.get("alertas_claude"):
        alertas = _receber_resposta_manual("alertas", "Alertas periciais", alertas)

# ── Downloads ─────────────────────────────────────────────────────────────────

st.success("✅ Concluído!")
st.subheader("📥 Downloads")

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

st.subheader("🧾 Auditoria técnica")
relatorio_pre = gerar_relatorio_preprocessamento_pdf(estrutura_pdf)
texto_sanitizado = gerar_texto_sanitizado(textos_paginas)
st.download_button("🧾 Estrutura PDF (.json)", gerar_json_bytes(estrutura_pdf),
    f"estrutura_pdf_{num}.json", "application/json")
st.download_button("🧾 Relatório pré-processamento (.md)", relatorio_pre.encode(),
    f"relatorio_preprocessamento_pdf_{num}.md", "text/markdown")
st.download_button("🧾 Texto sanitizado (.txt)", texto_sanitizado.encode(),
    f"texto_extraido_sanitizado_{num}.txt", "text/plain")
