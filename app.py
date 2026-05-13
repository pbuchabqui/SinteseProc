"""
app.py — Síntese de Decisões Trabalhista
Saídas: Word (.docx) | Markdown (.md) | Excel (.xlsx)
"""

import io, json, re
import streamlit as st
import fitz
from groq import Groq
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Síntese de Decisões", page_icon="⚖️")
st.title("⚖️ Síntese de Decisões Trabalhista")
st.caption("Upload PDF → extrair → baixar Word / Markdown / Excel")

def get_secret(k):
    try: return st.secrets[k]
    except Exception:
        import os; return os.getenv(k)

GROQ_KEY = get_secret("GROQ_API_KEY")
if not GROQ_KEY:
    st.error("GROQ_API_KEY não configurada. Adicione em Settings → Secrets.")
    st.stop()

# ── PDF ───────────────────────────────────────────────────────────────────────

def ler_pdf(b: bytes):
    doc = fitz.open(stream=b, filetype="pdf")
    pags = [f"[PÁGINA {i+1}]\n{p.get_text()}" for i, p in enumerate(doc)]
    doc.close()
    return "\n".join(pags), len(pags)

def buscar_secoes(txt: str):
    padroes = {
        "sentenca":    r"S\s*E\s*N\s*T\s*E\s*N\s*[CÇ]\s*A|VISTOS.*RELATADOS",
        "acordao":     r"A\s*C\s*[OÓ]\s*R\s*D\s*[AÃ]\s*O",
        "dispositivo": r"ISTO POSTO|DIANTE DO EXPOSTO|PELO EXPOSTO|DECIDO",
        "ficha":       r"FICHA FINANCEIRA|CONTRACHEQUE|HOLERITE|FOLHA DE PAGAMENTO",
        "ponto":       r"CART[AÃ]O DE PONTO|ESPELHO DE PONTO|REGISTRO DE PONTO",
    }
    linhas = txt.split("\n"); sec = {}
    for nome, p in padroes.items():
        for i, l in enumerate(linhas):
            if re.search(p, l, re.IGNORECASE):
                sec[nome] = "\n".join(linhas[i:i+200]); break
    return sec

def dados_basicos(txt: str):
    m = re.search(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", txt)
    return {"numero_processo": m.group() if m else "Não localizado"}

# ── IA (Groq) ─────────────────────────────────────────────────────────────────

MODELO_RACIOCINIO = "openai/gpt-oss-120b"

def ia(txt: str, instrucao: str) -> dict:
    modelo = get_secret("GROQ_MODEL") or "llama-3.3-70b-versatile"
    usa_raciocinio = modelo == MODELO_RACIOCINIO

    params = dict(
        model=modelo,
        stream=False,
        messages=[
            {"role":"system","content":(
                "Você é perito contábil trabalhista brasileiro. "
                "Responda APENAS com JSON válido, sem texto antes/depois, sem ```json```.")},
            {"role":"user","content":f"{instrucao}\n\nTEXTO:\n{txt[:25000]}"},
        ]
    )
    if usa_raciocinio:
        # gpt-oss-120b: parâmetros do modelo de raciocínio
        params["max_completion_tokens"] = 3000
        params["temperature"]           = 0.6
        params["top_p"]                 = 1
        params["reasoning_effort"]      = get_secret("GROQ_REASONING_EFFORT") or "medium"
    else:
        # llama-3.3-70b-versatile e outros: parâmetros padrão
        params["max_tokens"]  = 3000
        params["temperature"] = 0.1

    r   = Groq(api_key=GROQ_KEY).chat.completions.create(**params)
    raw = r.choices[0].message.content
    try: return json.loads(re.sub(r"```(?:json)?|```","",raw).strip())
    except: return {"erro": "JSON inválido", "texto_bruto": raw}

def ext_partes(txt):
    return ia(txt,"""Extraia do texto do processo trabalhista:
{"reclamante":"nome","cpf_reclamante":"000.000.000-00","adv_reclamante":"nome",
"oab_reclamante":"OAB/XX 00000","reclamada_1":"razão social","cnpj_reclamada_1":"",
"adv_reclamada_1":"nome","reclamada_2":"ou null","vara_trabalho":"ex: 1ª VT Porto Alegre/RS",
"data_admissao":"dd/mm/aaaa","data_demissao":"dd/mm/aaaa","data_ajuizamento":"dd/mm/aaaa",
"funcao":"cargo"}""")

def ext_decisoes(txt):
    return ia(txt,"""Extraia as decisões judiciais. Transcreva LITERALMENTE o dispositivo.
{"decisoes":[{"tipo":"Sentença|Acórdão|Embargos","data":"dd/mm/aaaa",
"dispositivo":"texto literal completo","resultado_reclamante":"procedente|improcedente|parcialmente",
"verbas_deferidas":["lista"]}]}""")

def ext_criterios(txt):
    return ia(txt,"""Extraia os critérios de liquidação.
{"criterios":{"base_salarial":"","periodo_apurado":{"inicio":"dd/mm/aaaa","fim":"dd/mm/aaaa"},
"jornada_contratual":"","jornada_real_apurada":"","divisor":"220|200|outro",
"adicional_horas_extras":"50%|100%","reflexos":["DSR","férias","13º","aviso","FGTS"],
"fgts":{"base":"todas as verbas","multa_40":true},"atualizacao_monetaria":"SELIC (ADC 58)",
"juros":"SELIC|1% am","inss_empregado":"descontar","inss_patronal":"a cargo da reclamada",
"ir":"tabela progressiva|isento","exclusoes_expressas":[],"observacoes":""}}""")

def ext_ficha(txt):
    return ia(txt,"""Extraia a ficha financeira. Liste TODAS as rubricas sem omitir nenhuma.
{"rubricas":["SALÁRIO BASE","HE 50%","..."],
"competencias":[{"competencia":"MM/AAAA",
"valores":{"SALÁRIO BASE":{"referencia":"220h","valor":3000.00}},
"total_proventos":0,"inss_base":0,"inss_desconto":0,"fgts_base":0}]}""")

def ext_ponto(txt):
    return ia(txt,"""Extraia o espelho de ponto com todos os registros.
{"registros":[{"data":"dd/mm/aaaa","dia_semana":"SEG",
"entradas_saidas":["08:00","12:00","13:00","18:00"],
"horas_trabalhadas":"8:00","horas_extras":"0:00",
"observacao":"DSR|Falta|Feriado|null"}]}""")

# ── Geração Word ──────────────────────────────────────────────────────────────

def gerar_word(bas, partes, decisoes, criterios) -> bytes:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)

    def h(t, n=1):
        x = doc.add_heading(t, level=n)
        x.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def campo(label, val):
        p = doc.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(str(val or "Não informado"))

    h("SÍNTESE DE DECISÕES PARA LIQUIDAÇÃO")
    h("1. Identificação do Processo", 2)
    campo("Número", bas.get("numero_processo"))

    p = partes if (partes and "erro" not in partes) else {}
    for lb, ch in [("Vara","vara_trabalho"),("Reclamante","reclamante"),
                   ("CPF","cpf_reclamante"),("Advogado reclamante","adv_reclamante"),
                   ("OAB reclamante","oab_reclamante"),("Reclamada","reclamada_1"),
                   ("CNPJ","cnpj_reclamada_1"),("Advogado reclamada","adv_reclamada_1"),
                   ("Admissão","data_admissao"),("Demissão","data_demissao"),
                   ("Ajuizamento","data_ajuizamento"),("Função","funcao")]:
        if p.get(ch): campo(lb, p[ch])
    if p.get("reclamada_2"): campo("2ª Reclamada", p["reclamada_2"])

    if decisoes and "erro" not in decisoes and decisoes.get("decisoes"):
        h("2. Decisões Judiciais", 2)
        for i, d in enumerate(decisoes["decisoes"], 1):
            h(f"2.{i} {d.get('tipo','Decisão')}", 3)
            campo("Data", d.get("data"))
            campo("Resultado", d.get("resultado_reclamante"))
            if d.get("verbas_deferidas"):
                campo("Verbas", ", ".join(d["verbas_deferidas"]))
            doc.add_paragraph().add_run("DISPOSITIVO:").bold = True
            doc.add_paragraph(d.get("dispositivo",""))

    if criterios and "erro" not in criterios and criterios.get("criterios"):
        h("3. Critérios de Liquidação", 2)
        c = criterios["criterios"]
        periodo = c.get("periodo_apurado",{})
        if periodo: campo("Período", f"{periodo.get('inicio')} a {periodo.get('fim')}")
        for lb, ch in [("Base salarial","base_salarial"),
                       ("Jornada contratual","jornada_contratual"),
                       ("Jornada real","jornada_real_apurada"),("Divisor","divisor"),
                       ("Adicional HE","adicional_horas_extras"),
                       ("Atualização","atualizacao_monetaria"),("Juros","juros"),
                       ("INSS empregado","inss_empregado"),("INSS patronal","inss_patronal"),
                       ("IR","ir"),("Observações","observacoes")]:
            if c.get(ch): campo(lb, c[ch])
        if c.get("reflexos"): campo("Reflexos", ", ".join(c["reflexos"]))
        fgts = c.get("fgts",{})
        if fgts: campo("FGTS", f"{fgts.get('base','')} — {'com' if fgts.get('multa_40') else 'sem'} multa 40%")
        if c.get("exclusoes_expressas"): campo("Exclusões", "; ".join(c["exclusoes_expressas"]))

    buf = io.BytesIO(); doc.save(buf); buf.seek(0)
    return buf.getvalue()

# ── Geração Markdown ──────────────────────────────────────────────────────────

def gerar_markdown(bas, partes, decisoes, criterios) -> str:
    md = ["# SÍNTESE DE DECISÕES PARA LIQUIDAÇÃO\n",
          "## 1. Identificação do Processo\n",
          f"**Número:** {bas.get('numero_processo','Não localizado')}"]

    p = partes if (partes and "erro" not in partes) else {}
    for lb, ch in [("Vara","vara_trabalho"),("Reclamante","reclamante"),
                   ("CPF","cpf_reclamante"),("Advogado reclamante","adv_reclamante"),
                   ("Reclamada","reclamada_1"),("CNPJ","cnpj_reclamada_1"),
                   ("Admissão","data_admissao"),("Demissão","data_demissao"),
                   ("Ajuizamento","data_ajuizamento"),("Função","funcao")]:
        if p.get(ch): md.append(f"**{lb}:** {p[ch]}")
    if p.get("reclamada_2"): md.append(f"**2ª Reclamada:** {p['reclamada_2']}")

    if decisoes and "erro" not in decisoes and decisoes.get("decisoes"):
        md.append("\n## 2. Decisões Judiciais\n")
        for i, d in enumerate(decisoes["decisoes"], 1):
            md.append(f"### 2.{i} {d.get('tipo','Decisão')}")
            md.append(f"**Data:** {d.get('data','')}  |  **Resultado:** {d.get('resultado_reclamante','')}")
            if d.get("verbas_deferidas"): md.append(f"**Verbas:** {', '.join(d['verbas_deferidas'])}")
            md.append(f"\n**DISPOSITIVO:**\n\n{d.get('dispositivo','')}\n")

    if criterios and "erro" not in criterios and criterios.get("criterios"):
        md.append("\n## 3. Critérios de Liquidação\n")
        c = criterios["criterios"]
        per = c.get("periodo_apurado",{})
        if per: md.append(f"**Período:** {per.get('inicio')} a {per.get('fim')}")
        for lb, ch in [("Base salarial","base_salarial"),("Jornada contratual","jornada_contratual"),
                       ("Jornada real","jornada_real_apurada"),("Divisor","divisor"),
                       ("Adicional HE","adicional_horas_extras"),("Atualização","atualizacao_monetaria"),
                       ("Juros","juros"),("INSS empregado","inss_empregado"),
                       ("INSS patronal","inss_patronal"),("IR","ir"),("Observações","observacoes")]:
            if c.get(ch): md.append(f"**{lb}:** {c[ch]}")
        if c.get("reflexos"): md.append(f"**Reflexos:** {', '.join(c['reflexos'])}")
        fgts = c.get("fgts",{})
        if fgts: md.append(f"**FGTS:** {fgts.get('base','')} — {'com' if fgts.get('multa_40') else 'sem'} multa 40%")
        if c.get("exclusoes_expressas"): md.append(f"**Exclusões:** {'; '.join(c['exclusoes_expressas'])}")

    return "\n\n".join(md)

# ── Geração Excel ─────────────────────────────────────────────────────────────

AZUL, BRANCO = "1F497D", "FFFFFF"

def _cab(c, bg=AZUL):
    c.font = Font(bold=True, color=BRANCO, size=11)
    c.fill = PatternFill("solid", fgColor=bg)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def _borda(c):
    b = Side(style="thin")
    c.border = Border(left=b, right=b, top=b, bottom=b)

def _campo_xl(ws, ln, label, val):
    ca = ws.cell(ln, 1, label); cb = ws.cell(ln, 2, val or "")
    ca.font = Font(bold=True); _borda(ca); _borda(cb)
    return ln + 1

def aba_liquidacao_xl(ws, bas, partes):
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 50
    h = ws.cell(1,1,"LIQUIDAÇÃO DE SENTENÇA")
    h.font = Font(bold=True, size=13, color=BRANCO)
    h.fill = PatternFill("solid", fgColor=AZUL)
    h.alignment = Alignment(horizontal="center")
    ws.merge_cells("A1:B1")
    p = partes if (partes and "erro" not in partes) else {}
    n = 3
    for lb, ch in [("Número do processo","numero_processo"),("",""),
                   ("Vara do Trabalho","vara_trabalho"),("",""),
                   ("Reclamante","reclamante"),("CPF","cpf_reclamante"),
                   ("Advogado reclamante","adv_reclamante"),("OAB","oab_reclamante"),("",""),
                   ("1ª Reclamada","reclamada_1"),("CNPJ","cnpj_reclamada_1"),
                   ("Advogado reclamada","adv_reclamada_1"),("",""),
                   ("Admissão","data_admissao"),("Demissão","data_demissao"),
                   ("Ajuizamento","data_ajuizamento"),("Função","funcao")]:
        if lb == "":
            n += 1; continue
        src = bas if ch == "numero_processo" else p
        n = _campo_xl(ws, n, lb, src.get(ch))
    if p.get("reclamada_2"):
        _campo_xl(ws, n, "2ª Reclamada", p["reclamada_2"])

def aba_pagamentos_xl(ws, ficha):
    if not ficha or "erro" in ficha or not ficha.get("competencias"):
        ws.cell(1,1,"Ficha financeira não encontrada no PDF."); return
    rubricas = ficha.get("rubricas",[])
    cabs = ["Competência"]
    for r in rubricas: cabs += [f"{r} Ref", f"{r} Valor"]
    cabs += ["Total Proventos","INSS Base","INSS Desconto","FGTS Base"]
    for ci, t in enumerate(cabs, 1):
        c = ws.cell(1, ci, t); _cab(c)
        ws.column_dimensions[get_column_letter(ci)].width = 16
    for li, comp in enumerate(ficha["competencias"], 2):
        col = 1; ws.cell(li, col, comp.get("competencia","")); col += 1
        for r in rubricas:
            v = comp.get("valores",{}).get(r,{})
            ws.cell(li, col, v.get("referencia","")); ws.cell(li, col+1, v.get("valor",0)); col += 2
        ws.cell(li,col,comp.get("total_proventos",0)); ws.cell(li,col+1,comp.get("inss_base",0))
        ws.cell(li,col+2,comp.get("inss_desconto",0)); ws.cell(li,col+3,comp.get("fgts_base",0))
    ws.freeze_panes = "B2"

def aba_ponto_xl(ws, ponto):
    if not ponto or "erro" in ponto or not ponto.get("registros"):
        ws.cell(1,1,"Espelho de ponto não encontrado no PDF."); return
    regs = ponto["registros"]
    n_max = max((len(r.get("entradas_saidas",[])) for r in regs), default=4)
    n_max = max(n_max, 2)
    cabs = ["Data","Dia"]
    for i in range(1, n_max//2+1): cabs += [f"E{i}", f"S{i}"]
    cabs += ["H.Trab.","H.Extra","Observação"]
    for ci, t in enumerate(cabs, 1):
        c = ws.cell(1,ci,t); _cab(c)
        ws.column_dimensions[get_column_letter(ci)].width = 12
    for li, reg in enumerate(regs, 2):
        ws.cell(li,1,reg.get("data","")); ws.cell(li,2,reg.get("dia_semana",""))
        for ci, hs in enumerate(reg.get("entradas_saidas",[]), 3):
            ws.cell(li, ci, hs)
        ultimo = 2 + n_max
        ws.cell(li,ultimo+1,reg.get("horas_trabalhadas",""))
        ws.cell(li,ultimo+2,reg.get("horas_extras",""))
        ws.cell(li,ultimo+3,reg.get("observacao",""))
    ws.freeze_panes = "A2"

def gerar_excel(bas, partes, ficha, ponto, inc_pag, inc_pto) -> bytes:
    wb = Workbook()
    ws = wb.active; ws.title = "LIQUIDACAO"
    aba_liquidacao_xl(ws, bas, partes)
    if inc_pag:
        aba_pagamentos_xl(wb.create_sheet("PAGAMENTOS"), ficha)
    if inc_pto:
        aba_ponto_xl(wb.create_sheet("PONTO"), ponto)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue()

# ── Interface ─────────────────────────────────────────────────────────────────

arq = st.file_uploader("📄 PDF do processo", type=["pdf"])
if not arq:
    st.info("Faça o upload do PDF para continuar.")
    st.stop()
st.success(f"{arq.name} ({arq.size/1024/1024:.1f} MB)")

st.subheader("O que extrair?")
c1, c2 = st.columns(2)
with c1:
    f_partes    = st.checkbox("Dados do processo e partes",       value=True)
    f_decisoes  = st.checkbox("Decisões judiciais (dispositivos)", value=True)
    f_criterios = st.checkbox("Critérios de liquidação",           value=True)
with c2:
    f_ficha = st.checkbox("Ficha financeira (holerites)")
    f_ponto = st.checkbox("Espelho de ponto")
st.caption("Cada item = ~1 chamada à IA (15–30 s). Ficha e ponto dependem do PDF conter esses documentos.")

st.subheader("Arquivos de saída")
c3, c4 = st.columns(2)
with c3:
    o_word = st.checkbox("Word (.docx)",     value=True)
    o_md   = st.checkbox("Markdown (.md)")
with c4:
    o_xl   = st.checkbox("Excel (.xlsx)")
    if o_xl:
        st.caption("Abas Excel:")
        st.checkbox("LIQUIDACAO (sempre incluída)", value=True, disabled=True)
        aba_pag = st.checkbox("PAGAMENTOS (ficha)", disabled=not f_ficha,
                              help="Marque 'Ficha financeira' acima para habilitar")
        aba_pto = st.checkbox("PONTO (espelho)", disabled=not f_ponto,
                              help="Marque 'Espelho de ponto' acima para habilitar")
    else:
        aba_pag = aba_pto = False

if not (o_word or o_md or o_xl):
    st.warning("Selecione pelo menos um arquivo de saída.")
    st.stop()

if not st.button("⚙️ Processar", type="primary"):
    st.stop()

# ── Processamento ─────────────────────────────────────────────────────────────

pdf  = arq.read()
bas = partes = decisoes = criterios = ficha = ponto = {}

with st.spinner("Lendo PDF..."):
    txt, npags = ler_pdf(pdf)
    bas  = dados_basicos(txt)
    secs = buscar_secoes(txt)

st.write(f"✅ {npags} páginas — **{bas.get('numero_processo')}**")

if f_partes:
    with st.spinner("Partes (IA)..."): partes = ext_partes(txt)
    st.write(f"✅ {partes.get('reclamante','?')} × {partes.get('reclamada_1','?')}"
             if "erro" not in partes else "⚠️ Partes: extração parcial")

if f_decisoes:
    t = (secs.get("sentenca","") + "\n" + secs.get("acordao","")) or txt
    with st.spinner("Decisões (IA)..."): decisoes = ext_decisoes(t)
    n = len(decisoes.get("decisoes",[]))
    st.write(f"✅ {n} decisão(ões)" if n else "⚠️ Nenhuma decisão localizada")

if f_criterios:
    t = secs.get("dispositivo","") or secs.get("sentenca","") or txt
    with st.spinner("Critérios (IA)..."): criterios = ext_criterios(t)
    st.write("✅ Critérios extraídos" if "erro" not in criterios else "⚠️ Critérios: extração parcial")

if f_ficha:
    t = secs.get("ficha","") or txt
    with st.spinner("Ficha financeira (IA)..."): ficha = ext_ficha(t)
    n = len(ficha.get("competencias",[]))
    st.write(f"✅ Ficha: {n} competência(s)" if n else "⚠️ Ficha não localizada no PDF")

if f_ponto:
    t = secs.get("ponto","") or txt
    with st.spinner("Espelho de ponto (IA)..."): ponto = ext_ponto(t)
    n = len(ponto.get("registros",[]))
    st.write(f"✅ Ponto: {n} registro(s)" if n else "⚠️ Ponto não localizado no PDF")

# ── Downloads ─────────────────────────────────────────────────────────────────

st.success("✅ Concluído!")
st.subheader("📥 Downloads")
num = bas.get("numero_processo","processo").replace("-","").replace(".","")

if o_word:
    with st.spinner("Gerando Word..."):
        wb = gerar_word(bas, partes, decisoes, criterios)
    st.download_button("📄 Word (.docx)", wb, f"sintese_{num}.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

if o_md:
    with st.spinner("Gerando Markdown..."):
        md = gerar_markdown(bas, partes, decisoes, criterios)
    st.download_button("📝 Markdown (.md)", md.encode(), f"sintese_{num}.md", "text/markdown")

if o_xl:
    with st.spinner("Gerando Excel..."):
        xl = gerar_excel(bas, partes, ficha, ponto, aba_pag, aba_pto)
    st.download_button("📊 Excel (.xlsx)", xl, f"liquidacao_{num}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
