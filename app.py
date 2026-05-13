"""
app.py — Síntese de Decisões Trabalhista
IA usada APENAS para critérios de liquidação (interpretação jurídica).
Todo o resto é regex + PyMuPDF (determinístico, rápido, sem custo).
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

# ── Config ────────────────────────────────────────────────────────────────────

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

# ══════════════════════════════════════════════════════════════════════════════
# BLOCO 1 — EXTRAÇÃO DETERMINÍSTICA (sem IA)
# ══════════════════════════════════════════════════════════════════════════════

def ler_pdf(b: bytes) -> tuple[fitz.Document, str, str, list, int]:
    """Abre o PDF e retorna (doc, capa, texto_completo, toc, total_paginas).
    capa = texto da página 1 (cabeçalho PJe com partes explícitas).
    toc  = sumário estruturado do PDF (lista de [nivel, titulo, pagina]).
    """
    doc = fitz.open(stream=b, filetype="pdf")
    capa = doc[0].get_text() if len(doc) > 0 else ""
    toc  = doc.get_toc()  # sumário embutido — disponível em todos os PDFs PJe
    paginas = [f"[PÁGINA {i+1}]\n{p.get_text()}" for i, p in enumerate(doc)]
    return doc, capa, "\n".join(paginas), toc, len(paginas)


# ── 1.1 Dados do processo (regex) ────────────────────────────────────────────

def extrair_dados(txt: str, capa: str = "") -> dict:
    """
    capa = texto da página 1 do PJe (fonte mais confiável para partes).
    Partes: lidas da capa primeiro; fallback no corpo do processo.
    CPF/CNPJ: exigem label próximo para evitar falsos positivos de URLs PJe.
    Datas: buscadas nos dois sentidos (label→data e data→label).
    """
    def primeiro(padrao, texto=txt, grupo=0):
        m = re.search(padrao, texto, re.IGNORECASE)
        return m.group(grupo) if m else None

    def todos(padrao, texto=txt):
        return re.findall(padrao, texto, re.IGNORECASE)

    # ── Número do processo ──
    numero = primeiro(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

    # ── Vara do trabalho ──
    vara = primeiro(r"(\d+[ªº°]?\s*VARA\s+DO\s+TRABALHO[^\n]{0,60})", grupo=1) \
        or primeiro(r"(VARA\s+DO\s+TRABALHO[^\n]{0,60})", grupo=1)
    if vara: vara = vara.strip().rstrip(".,;:()")

    # ── CPF — exige label "CPF" a ≤80 chars antes, para não pegar hashes de URL PJe ──
    def formatar_cpf(d):
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else d

    cpf = None
    m_cpf = re.search(
        r"CPF[/\w\s\.]{0,30}?n?[º.]?\s*(\d{3}[\. ]?\d{3}[\. ]?\d{3}[\-\s]?\d{2})(?!\d)",
        txt, re.IGNORECASE
    )
    if m_cpf:
        d = re.sub(r"\D", "", m_cpf.group(1))
        cpf = formatar_cpf(d) if len(d) == 11 else None

    # ── CNPJ ──
    def formatar_cnpj(raw):
        if not raw: return None
        d = re.sub(r"\D", "", raw)
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else raw

    cnpj_raw = primeiro(
        r"CNPJ[^\d]{0,20}(\d{2}[\.-]?\d{3}[\.-]?\d{3}[/]?\d{4}[-]?\d{2})", grupo=1
    ) or primeiro(r"(\d{2}[\.-]?\d{3}[\.-]?\d{3}[/]\d{4}[-]\d{2})", grupo=1)
    cnpj = formatar_cnpj(cnpj_raw)

    # ── OABs — normalizar e deduplicar ──
    def normalizar_oab(s):
        m = re.search(r"OAB[/\s]*([A-Z]{2})[/\s#nº°.]*\s*([\d\.]+)", s, re.IGNORECASE)
        if m:
            num = re.sub(r"\.", "", m.group(2))  # remove pontos do número
            return f"OAB/{m.group(1).upper()} {num}"
        return s.strip()
    oabs_raw = todos(r"OAB[/\s]*[A-Z]{2}[/\s#nº°.]*\s*[\d\.]+")
    oabs = list(dict.fromkeys(normalizar_oab(o) for o in oabs_raw))[:6]

    # ── Partes — da CAPA (página 1 PJe) ──
    # Formato canônico: RECLAMANTE: NOME / RECLAMADO: NOME / ADVOGADO: NOME
    def nome_da_capa(label, texto=None):
        src = texto or capa or txt
        m = re.search(rf"^{label}:\s*(.+)$", src, re.IGNORECASE | re.MULTILINE)
        if m:
            nome = m.group(1).strip().rstrip(".,;")
            if not re.search(r"PERITO|ADVOGADO|RECLAMANTE|RECLAMADO", nome, re.IGNORECASE):
                return nome
        return None

    reclamante = nome_da_capa("RECLAMANTE") or nome_da_capa("AUTOR[AE]?")
    reclamada  = nome_da_capa("RECLAMADO[A]?") or nome_da_capa("R[EÉ]U") \
              or nome_da_capa("EXECUTAD[AO]")

    advs_capa = re.findall(r"^ADVOGADO:\s*(.+)$", capa or "", re.IGNORECASE | re.MULTILINE)
    adv_rec   = advs_capa[0].strip() if len(advs_capa) > 0 else None
    adv_emp   = advs_capa[1].strip() if len(advs_capa) > 1 else None

    # ── Datas — busca bidirecional (label→data e data→label) ──
    def data_por_label(labels, fonte=None):
        src = fonte or txt
        for label in labels:
            # sentido normal: "Admissão: 12/06/2021" ou "Admissão ... 12/06/2021"
            m = re.search(
                rf"{label}[^:\n]{{0,30}}:\s*(\d{{2}}/\d{{2}}/\d{{4}})"
                rf"|{label}[^\d\n]{{0,60}}(\d{{2}}/\d{{2}}/\d{{4}})",
                src, re.IGNORECASE
            )
            if m: return m.group(1) or m.group(2)
            # sentido inverso: "12/06/2021 - Admissão" (formato CTPS)
            m2 = re.search(rf"(\d{{2}}/\d{{2}}/\d{{4}})\s*[-–]\s*{label}", src, re.IGNORECASE)
            if m2: return m2.group(1)
        return None

    # Ajuizamento — usar Data da Autuação da capa (mais preciso)
    data_ajuizamento = data_por_label([r"Data\s+da\s+Autua[çc][aã]o",
                                       r"Autua[çc][aã]o"], fonte=capa)                     or data_por_label([r"ajuizamento", r"distribui[çc][aã]o"])
    data_admissao  = data_por_label([r"admiss[aã]o", r"admitid[ao]"])
    data_demissao  = data_por_label([r"Rescis[aã]o\s+Contratual", r"demiss[aã]o",
                                     r"despedid[ao]", r"desligad[ao]"])

    # Função/cargo — padrão CTPS
    funcao = None
    m_func = re.search(r"Cargo\s+exercido\s+de\s+([A-Za-záéíóúâêîôûãõÁÉÍÓÚ\s\.]{3,50}?)(?:\n|$|,|\d)",
                       txt, re.IGNORECASE)
    if m_func: funcao = m_func.group(1).strip()

    return {
        "numero_processo":  numero or "Não localizado",
        "vara_trabalho":    vara,
        "reclamante":       reclamante,
        "cpf_reclamante":   cpf,
        "reclamada_1":      reclamada,
        "cnpj_reclamada_1": cnpj,
        "oabs":             oabs,
        "adv_reclamante":   adv_rec,
        "adv_reclamada_1":  adv_emp,
        "data_admissao":    data_admissao,
        "data_demissao":    data_demissao,
        "data_ajuizamento": data_ajuizamento,
        "funcao":           funcao,
    }

# ── 1.2 Localizar seções (busca bidirecional) ─────────────────────────────────

def buscar_secoes(doc: fitz.Document, toc: list, txt: str) -> dict:
    """
    Localiza seções usando o sumário (TOC) do PDF quando disponível.
    O TOC do PJe lista cada documento com tipo, data e página exata.
    Fallback para busca textual se o TOC não tiver informação suficiente.
    """
    MAPA_TIPOS = {
        "Sentença":               "sentenca",
        "Acórdão":                "acordao",
        "Embargos de Declaração": "embargos",
        "Decisão":                "decisao",
        "Ficha Financeira":       "ficha",
        "Ficha de Registro":      "ficha",
        "Cartão de Ponto":        "ponto",
        "Espelho de Ponto":       "ponto",
    }
    RELEVANTES = {"sentenca", "acordao", "embargos", "decisao", "ficha", "ponto"}
    npags = len(doc)
    sec = {}

    if toc:
        # ── Abordagem primária: navegar pelo TOC ──
        entradas = []
        for idx, entry in enumerate(toc):
            _, titulo, pag_ini = entry
            tipo_cat = next(
                (cat for tipo_str, cat in MAPA_TIPOS.items()
                 if tipo_str.lower() in titulo.lower()),
                None
            )
            if not tipo_cat or tipo_cat not in RELEVANTES:
                continue
            # Página final = início da próxima entrada - 1
            pag_fim = (toc[idx+1][2] - 1) if idx+1 < len(toc) else npags
            entradas.append((tipo_cat, titulo, pag_ini-1, pag_fim-1))  # 0-based

        # Para cada categoria, consolidar texto das páginas relevantes
        # Decisões: agrupadas por categoria, em ordem cronológica
        from collections import defaultdict
        grupos = defaultdict(list)
        for cat, titulo, p0, p1 in entradas:
            texto_doc = "\n".join(doc[i].get_text() for i in range(p0, min(p1+1, npags)))
            grupos[cat].append(f"\n--- {titulo} ---\n{texto_doc}")

        for cat, blocos in grupos.items():
            sec[cat] = "\n\n".join(blocos)

    # ── Fallback textual para PDFs sem TOC ──
    if not sec.get("sentenca") or not sec.get("dispositivo"):
        linhas = txt.split("\n")
        padroes_fallback = {
            "sentenca":    r"VISTOS[,\s]+RELATADOS|VISTOS[,\s]+ETC",
            "acordao":     r"A\s*C\s*[OÓ]\s*R\s*D\s*[AÃ]\s*O",
            "dispositivo": r"(?:^|\n)\s*Condeno\b|JULGO\s+PROCED|ISTO\s+POSTO",
        }
        for nome, p in padroes_fallback.items():
            if nome in sec: continue
            for i in range(len(linhas)-1, -1, -1):
                if re.search(p, linhas[i], re.IGNORECASE | re.MULTILINE):
                    sec[nome] = "\n".join(linhas[i:i+600])
                    break

    # Ficha e ponto: busca textual (estrutura de tabela, não aparece no TOC tipo)
    if not sec.get("ficha"):
        linhas = txt.split("\n")
        for i, l in enumerate(linhas):
            if re.search(r"FICHA FINANCEIRA|CONTRACHEQUE|HOLERITE", l, re.IGNORECASE):
                sec["ficha"] = "\n".join(linhas[i:i+600]); break
    if not sec.get("ponto"):
        linhas = txt.split("\n")
        for i, l in enumerate(linhas):
            if re.search(r"CART[AÃ]O DE PONTO|ESPELHO DE PONTO", l, re.IGNORECASE):
                sec["ponto"] = "\n".join(linhas[i:i+600]); break

    return sec
# ── 1.3 Extrair decisões (sem IA — extração literal de texto) ─────────────────

MARCADORES_TIPO = {
    "Sentença":              r"S\s*E\s*N\s*T\s*E\s*N\s*[CÇ]\s*A|VISTOS[,\s]+RELATADOS",
    "Acórdão":               r"A\s*C\s*[OÓ]\s*R\s*D\s*[AÃ]\s*O",
    "Embargos de Declaração":r"EMBARGOS\s+DE\s+DECLARA[CÇ][AÃ]O",
}
MARCADORES_DISPOSITIVO = [
    r"ISTO\s+POSTO", r"DIANTE\s+DO\s+EXPOSTO", r"PELO\s+EXPOSTO",
    r"DECIDO\s*[:;]", r"DECIDE-SE", r"JULGO",
]
MARCADORES_FIM_DISPOSITIVO = [
    r"Intimem-se", r"Cumpra-se", r"Publique-se", r"Registre-se",
    r"Após\s+o\s+trânsito", r"P\.R\.I", r"Intime-se",
]
VERBAS_CONHECIDAS = [
    "horas extras", "adicional noturno", "adicional de periculosidade",
    "adicional de insalubridade", "férias", "13º salário", "aviso prévio",
    "FGTS", "multa do art. 477", "multa do art. 467", "dano moral",
    "dano material", "diferenças salariais", "equiparação salarial",
    "intervalo intrajornada", "DSR", "vale-transporte", "vale-alimentação",
    "horas in itinere", "sobreaviso", "prontidão",
]

def extrair_decisoes(txt: str) -> list[dict]:
    """
    Extrai decisões por padrão de texto, sem IA.
    - Tipo: regex no cabeçalho da seção
    - Data: regex (dd/mm/aaaa)
    - Dispositivo: extração literal entre marcador e fim
    - Resultado: keyword search
    - Verbas: lista pré-definida
    """
    decisoes = []
    linhas = txt.split("\n")

    i = 0
    while i < len(linhas):
        linha = linhas[i]

        # Detectar início de uma decisão
        tipo_encontrado = None
        for tipo, padrao in MARCADORES_TIPO.items():
            if re.search(padrao, linha, re.IGNORECASE):
                tipo_encontrado = tipo
                break

        if not tipo_encontrado:
            i += 1
            continue

        # Janela de até 600 linhas a partir do marcador
        janela = linhas[i:i+600]
        bloco  = "\n".join(janela)

        # Data — primeira dd/mm/aaaa na janela
        m_data = re.search(r"\d{2}/\d{2}/\d{4}", bloco)
        data = m_data.group() if m_data else None

        # Dispositivo — do marcador até o fim
        dispositivo = ""
        inicio_disp = None
        for j, l in enumerate(janela):
            if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_DISPOSITIVO):
                inicio_disp = j
                break

        if inicio_disp is not None:
            linhas_disp = []
            for l in janela[inicio_disp:]:
                if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_FIM_DISPOSITIVO):
                    linhas_disp.append(l)
                    break
                linhas_disp.append(l)
            dispositivo = "\n".join(linhas_disp).strip()

        # Resultado — keyword
        resultado = "parcialmente procedente"  # default TRT
        if re.search(r"JULGO\s+IMPROCEDENTE|pedidos?\s+improcedentes?", bloco, re.IGNORECASE):
            resultado = "improcedente"
        elif re.search(r"JULGO\s+PROCEDENTE[^S]|pedidos?\s+procedentes?[^S]", bloco, re.IGNORECASE):
            resultado = "procedente"
        elif re.search(r"parcialmente|em\s+parte", bloco, re.IGNORECASE):
            resultado = "parcialmente procedente"

        # Verbas — lista pré-definida
        verbas = [v for v in VERBAS_CONHECIDAS
                  if re.search(re.escape(v), bloco, re.IGNORECASE)]

        if dispositivo or tipo_encontrado:
            decisoes.append({
                "tipo":                   tipo_encontrado,
                "data":                   data,
                "dispositivo":            dispositivo or "(dispositivo não localizado)",
                "resultado_reclamante":   resultado,
                "verbas_deferidas":       verbas,
            })
            i += 400  # pular para depois desta decisão
        else:
            i += 1

    return decisoes


# ── 1.4 Ficha financeira (PyMuPDF tabelas — sem IA) ───────────────────────────

def extrair_ficha(doc: fitz.Document) -> dict:
    """
    Extrai ficha financeira usando detecção de tabelas do PyMuPDF.
    Sem IA — dados tabulares são determinísticos.
    """
    rubricas_set = set()
    competencias = []

    for pagina in doc:
        texto_pag = pagina.get_text()
        # Verificar se esta página tem ficha financeira
        if not re.search(
            r"FICHA FINANCEIRA|CONTRACHEQUE|HOLERITE|FOLHA DE PAGAMENTO",
            texto_pag, re.IGNORECASE
        ):
            continue

        try:
            tabelas = pagina.find_tables()
        except Exception:
            continue

        for tabela in tabelas:
            try:
                linhas = tabela.extract()
            except Exception:
                continue
            if not linhas or len(linhas) < 2:
                continue

            cabecalho = [str(c).strip() if c else "" for c in linhas[0]]

            # Detectar coluna de competência
            col_comp = next(
                (i for i, c in enumerate(cabecalho)
                 if re.search(r"compet[êe]ncia|m[êe]s|per[íi]odo", c, re.IGNORECASE)),
                0
            )

            for linha in linhas[1:]:
                if not linha or not any(linha):
                    continue
                comp_val = str(linha[col_comp]).strip() if linha[col_comp] else ""
                if not re.search(r"\d{2}/\d{4}|\d{4}", comp_val):
                    continue

                valores = {}
                for ci, cel in enumerate(linha):
                    if ci == col_comp: continue
                    nome_col = cabecalho[ci] if ci < len(cabecalho) else f"col_{ci}"
                    if not nome_col: continue
                    val_str = str(cel).strip() if cel else ""
                    # Tentar converter para float
                    val_num = None
                    try:
                        val_num = float(
                            val_str.replace(".", "").replace(",", ".").replace("R$","").strip()
                        )
                    except (ValueError, AttributeError):
                        pass
                    if val_str:
                        rubricas_set.add(nome_col)
                        valores[nome_col] = {"referencia": "", "valor": val_num or val_str}

                if valores:
                    competencias.append({
                        "competencia": comp_val,
                        "valores": valores,
                        "total_proventos": None,
                        "inss_base": None,
                        "inss_desconto": None,
                        "fgts_base": None,
                    })

    if not competencias:
        return {"erro": "Ficha não localizada ou sem tabelas detectáveis no PDF"}

    return {"rubricas": sorted(rubricas_set), "competencias": competencias}


# ── 1.5 Espelho de ponto (PyMuPDF tabelas — sem IA) ───────────────────────────

def extrair_ponto(doc: fitz.Document) -> dict:
    """
    Extrai espelho de ponto usando detecção de tabelas do PyMuPDF.
    Sem IA — dados tabulares são determinísticos.
    """
    registros = []
    DIAS = {"SEG","TER","QUA","QUI","SEX","SÁB","SAB","DOM"}

    for pagina in doc:
        texto_pag = pagina.get_text()
        if not re.search(
            r"CART[AÃ]O DE PONTO|ESPELHO DE PONTO|REGISTRO DE PONTO",
            texto_pag, re.IGNORECASE
        ):
            continue

        try:
            tabelas = pagina.find_tables()
        except Exception:
            continue

        for tabela in tabelas:
            try:
                linhas = tabela.extract()
            except Exception:
                continue
            if not linhas or len(linhas) < 2:
                continue

            for linha in linhas[1:]:
                if not linha: continue
                celulas = [str(c).strip() if c else "" for c in linha]

                # Identificar célula de data
                data = next(
                    (c for c in celulas if re.match(r"\d{2}/\d{2}/\d{4}", c)),
                    None
                )
                if not data: continue

                # Dia da semana
                dia = next((c for c in celulas if c.upper() in DIAS), "")

                # Horários (HH:MM)
                horarios = [c for c in celulas if re.match(r"\d{2}:\d{2}", c)]

                # Horas trabalhadas e extras (último par de HH:MM costuma ser totais)
                ht = horarios[-2] if len(horarios) >= 2 else ""
                he = horarios[-1] if len(horarios) >= 1 else ""

                # Observação
                obs = next(
                    (c for c in celulas
                     if re.search(r"DSR|FALTA|FERIADO|FOLGA|AFASTAMENTO|FÉRIAS", c, re.IGNORECASE)),
                    ""
                )

                registros.append({
                    "data":             data,
                    "dia_semana":       dia,
                    "entradas_saidas":  horarios[:-2] if len(horarios) > 2 else horarios,
                    "horas_trabalhadas":ht,
                    "horas_extras":     he,
                    "observacao":       obs,
                })

    if not registros:
        return {"erro": "Ponto não localizado ou sem tabelas detectáveis no PDF"}

    return {"registros": registros}


# ══════════════════════════════════════════════════════════════════════════════
# BLOCO 2 — IA (apenas critérios de liquidação)
# ══════════════════════════════════════════════════════════════════════════════

MODELO_RACIOCINIO = "openai/gpt-oss-120b"

def chamar_ia(txt: str, instrucao: str, limite: int = 30000) -> dict:
    """Chamada única ao Groq — usada apenas para critérios de liquidação."""
    modelo = get_secret("GROQ_MODEL") or "llama-3.3-70b-versatile"
    usa_raciocinio = modelo == MODELO_RACIOCINIO

    params = dict(
        model=modelo, stream=False,
        messages=[
            {"role":"system","content":(
                "Você é perito contábil trabalhista brasileiro. "
                "Responda APENAS com JSON válido, sem texto antes/depois, sem ```json```.")},
            {"role":"user","content":f"{instrucao}\n\nTEXTO:\n{txt[:limite]}"},
        ]
    )
    if usa_raciocinio:
        params.update({"max_completion_tokens":3000,"temperature":0.6,"top_p":1,
                       "reasoning_effort": get_secret("GROQ_REASONING_EFFORT") or "medium"})
    else:
        params.update({"max_tokens":3000,"temperature":0.1})

    try:
        r   = Groq(api_key=GROQ_KEY).chat.completions.create(**params)
        raw = r.choices[0].message.content
        return json.loads(re.sub(r"```(?:json)?|```","",raw).strip())
    except json.JSONDecodeError:
        return {"erro": "JSON inválido", "texto_bruto": raw}
    except Exception as e:
        msg = str(e)
        if "rate_limit" in msg.lower() or "429" in msg:
            return {"erro": "Rate limit atingido. Aguarde 1 minuto e tente novamente."}
        return {"erro": f"Erro na API: {msg}"}


def ext_criterios(dispositivo: str) -> dict:
    """
    Única chamada de IA no sistema.
    Interpreta o dispositivo da sentença para extrair parâmetros de liquidação.
    Isso exige compreensão semântica — regex não resolve.
    """
    return chamar_ia(dispositivo, """
Analise o dispositivo da sentença/acórdão trabalhista e extraia os critérios de liquidação.

{
  "criterios": {
    "base_salarial": "descrição da base (ex: última remuneração R$ X)",
    "periodo_apurado": {"inicio": "dd/mm/aaaa", "fim": "dd/mm/aaaa"},
    "jornada_contratual": "ex: 8h diárias / 44h semanais",
    "jornada_real_apurada": "ex: 10h diárias conforme cartões de ponto",
    "divisor": "220 ou 200 ou outro valor",
    "adicional_horas_extras": "50% ou 100%",
    "reflexos": ["DSR", "férias", "13º salário", "aviso prévio", "FGTS"],
    "fgts": {"base": "todas as verbas deferidas", "multa_40": true},
    "atualizacao_monetaria": "SELIC (ADC 58/59) ou outro índice",
    "juros": "SELIC ou 1% ao mês",
    "inss_empregado": "descontar na fonte conforme tabela progressiva",
    "inss_patronal": "a cargo da reclamada",
    "ir": "tabela progressiva ou isento",
    "exclusoes_expressas": ["ex: dano moral não integra base FGTS"],
    "observacoes": "outros critérios relevantes não listados acima"
  }
}
""", limite=30000)


# ══════════════════════════════════════════════════════════════════════════════
# BLOCO 3 — GERAÇÃO DE ARQUIVOS
# ══════════════════════════════════════════════════════════════════════════════

def gerar_word(dados, decisoes, criterios) -> bytes:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)

    def h(t, n=1):
        x = doc.add_heading(t, level=n)
        x.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def campo(label, val):
        if not val: return
        p = doc.add_paragraph()
        p.add_run(f"{label}: ").bold = True
        p.add_run(str(val))

    h("SÍNTESE DE DECISÕES PARA LIQUIDAÇÃO")

    h("1. Identificação do Processo", 2)
    campo("Número",    dados.get("numero_processo"))
    campo("Vara",      dados.get("vara_trabalho"))
    campo("Reclamante",dados.get("reclamante"))
    campo("CPF",       dados.get("cpf_reclamante"))
    campo("Reclamada", dados.get("reclamada_1"))
    campo("CNPJ",      dados.get("cnpj_reclamada_1"))
    campo("Advogado (reclamante)", dados.get("adv_reclamante"))
    campo("Advogado (reclamada)",  dados.get("adv_reclamada_1"))
    campo("Admissão",    dados.get("data_admissao"))
    campo("Demissão",    dados.get("data_demissao"))
    campo("Ajuizamento", dados.get("data_ajuizamento"))
    if dados.get("oabs"):
        campo("OABs encontradas", " | ".join(dados["oabs"]))

    if decisoes:
        h("2. Decisões Judiciais", 2)
        for i, d in enumerate(decisoes, 1):
            h(f"2.{i} {d.get('tipo','Decisão')}", 3)
            campo("Data",      d.get("data"))
            campo("Resultado", d.get("resultado_reclamante"))
            if d.get("verbas_deferidas"):
                campo("Verbas identificadas", ", ".join(d["verbas_deferidas"]))
            doc.add_paragraph().add_run("DISPOSITIVO:").bold = True
            doc.add_paragraph(d.get("dispositivo",""))

    if criterios and "erro" not in criterios and criterios.get("criterios"):
        h("3. Critérios de Liquidação", 2)
        c = criterios["criterios"]
        per = c.get("periodo_apurado",{})
        if per: campo("Período", f"{per.get('inicio')} a {per.get('fim')}")
        for lb, ch in [
            ("Base salarial","base_salarial"),
            ("Jornada contratual","jornada_contratual"),
            ("Jornada real apurada","jornada_real_apurada"),
            ("Divisor","divisor"),
            ("Adicional HE","adicional_horas_extras"),
            ("Atualização monetária","atualizacao_monetaria"),
            ("Juros","juros"),
            ("INSS empregado","inss_empregado"),
            ("INSS patronal","inss_patronal"),
            ("IR","ir"),
            ("Observações","observacoes"),
        ]:
            campo(lb, c.get(ch))
        if c.get("reflexos"):
            campo("Reflexos", ", ".join(c["reflexos"]))
        fgts = c.get("fgts",{})
        if fgts:
            campo("FGTS", f"{fgts.get('base','')} — {'com' if fgts.get('multa_40') else 'sem'} multa 40%")
        if c.get("exclusoes_expressas"):
            campo("Exclusões expressas", "; ".join(c["exclusoes_expressas"]))

    buf = io.BytesIO(); doc.save(buf); buf.seek(0)
    return buf.getvalue()


def gerar_markdown(dados, decisoes, criterios) -> str:
    md = ["# SÍNTESE DE DECISÕES PARA LIQUIDAÇÃO\n",
          "## 1. Identificação do Processo\n"]

    for lb, ch in [
        ("Número","numero_processo"),("Vara","vara_trabalho"),
        ("Reclamante","reclamante"),("CPF","cpf_reclamante"),
        ("Reclamada","reclamada_1"),("CNPJ","cnpj_reclamada_1"),
        ("Advogado reclamante","adv_reclamante"),
        ("Advogado reclamada","adv_reclamada_1"),
        ("Admissão","data_admissao"),("Demissão","data_demissao"),
        ("Ajuizamento","data_ajuizamento"),
    ]:
        if dados.get(ch): md.append(f"**{lb}:** {dados[ch]}")
    if dados.get("oabs"):
        md.append(f"**OABs:** {' | '.join(dados['oabs'])}")

    if decisoes:
        md.append("\n## 2. Decisões Judiciais\n")
        for i, d in enumerate(decisoes, 1):
            md.append(f"### 2.{i} {d.get('tipo','Decisão')}")
            md.append(f"**Data:** {d.get('data','')}  |  **Resultado:** {d.get('resultado_reclamante','')}")
            if d.get("verbas_deferidas"):
                md.append(f"**Verbas:** {', '.join(d['verbas_deferidas'])}")
            md.append(f"\n**DISPOSITIVO:**\n\n{d.get('dispositivo','')}\n")

    if criterios and "erro" not in criterios and criterios.get("criterios"):
        md.append("\n## 3. Critérios de Liquidação\n")
        c = criterios["criterios"]
        per = c.get("periodo_apurado",{})
        if per: md.append(f"**Período:** {per.get('inicio')} a {per.get('fim')}")
        for lb, ch in [
            ("Base salarial","base_salarial"),("Jornada contratual","jornada_contratual"),
            ("Jornada real","jornada_real_apurada"),("Divisor","divisor"),
            ("Adicional HE","adicional_horas_extras"),("Atualização","atualizacao_monetaria"),
            ("Juros","juros"),("INSS empregado","inss_empregado"),
            ("INSS patronal","inss_patronal"),("IR","ir"),("Observações","observacoes"),
        ]:
            if c.get(ch): md.append(f"**{lb}:** {c[ch]}")
        if c.get("reflexos"): md.append(f"**Reflexos:** {', '.join(c['reflexos'])}")
        fgts = c.get("fgts",{})
        if fgts: md.append(f"**FGTS:** {fgts.get('base','')} — {'com' if fgts.get('multa_40') else 'sem'} multa 40%")
        if c.get("exclusoes_expressas"):
            md.append(f"**Exclusões:** {'; '.join(c['exclusoes_expressas'])}")

    return "\n\n".join(md)


AZUL, BRANCO = "1F497D", "FFFFFF"

def _cab(c):
    c.font = Font(bold=True, color=BRANCO, size=11)
    c.fill = PatternFill("solid", fgColor=AZUL)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def _borda(c):
    b = Side(style="thin")
    c.border = Border(left=b, right=b, top=b, bottom=b)

def _campo_xl(ws, ln, label, val):
    ca = ws.cell(ln,1,label); cb = ws.cell(ln,2,val or "")
    ca.font = Font(bold=True); _borda(ca); _borda(cb)
    return ln+1

def aba_liquidacao_xl(ws, dados):
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 50
    h = ws.cell(1,1,"LIQUIDAÇÃO DE SENTENÇA")
    h.font = Font(bold=True,size=13,color=BRANCO)
    h.fill = PatternFill("solid",fgColor=AZUL)
    h.alignment = Alignment(horizontal="center")
    ws.merge_cells("A1:B1")
    n = 3
    for lb, ch in [
        ("Número do processo","numero_processo"),("",""),
        ("Vara do Trabalho","vara_trabalho"),("",""),
        ("Reclamante","reclamante"),("CPF","cpf_reclamante"),
        ("Advogado reclamante","adv_reclamante"),("",""),
        ("1ª Reclamada","reclamada_1"),("CNPJ","cnpj_reclamada_1"),
        ("Advogado reclamada","adv_reclamada_1"),("",""),
        ("Admissão","data_admissao"),("Demissão","data_demissao"),
        ("Ajuizamento","data_ajuizamento"),
    ]:
        if lb == "": n += 1; continue
        n = _campo_xl(ws, n, lb, dados.get(ch))

def aba_pagamentos_xl(ws, ficha):
    if not ficha or "erro" in ficha or not ficha.get("competencias"):
        ws.cell(1,1,"Ficha não localizada ou sem tabelas detectáveis."); return
    rubricas = ficha.get("rubricas",[])
    cabs = ["Competência"]
    for r in rubricas: cabs += [f"{r} Ref", f"{r} Valor"]
    cabs += ["Total Proventos","INSS Base","INSS Desconto","FGTS Base"]
    for ci,t in enumerate(cabs,1):
        c = ws.cell(1,ci,t); _cab(c)
        ws.column_dimensions[get_column_letter(ci)].width = 16
    for li, comp in enumerate(ficha["competencias"],2):
        col=1; ws.cell(li,col,comp.get("competencia","")); col+=1
        for r in rubricas:
            v = comp.get("valores",{}).get(r,{})
            ws.cell(li,col,v.get("referencia","")); ws.cell(li,col+1,v.get("valor",0)); col+=2
        ws.cell(li,col,comp.get("total_proventos",""))
        ws.cell(li,col+1,comp.get("inss_base",""))
        ws.cell(li,col+2,comp.get("inss_desconto",""))
        ws.cell(li,col+3,comp.get("fgts_base",""))
    ws.freeze_panes = "B2"

def aba_ponto_xl(ws, ponto):
    if not ponto or "erro" in ponto or not ponto.get("registros"):
        ws.cell(1,1,"Ponto não localizado ou sem tabelas detectáveis."); return
    regs = ponto["registros"]
    n_max = max((len(r.get("entradas_saidas",[])) for r in regs), default=4)
    n_max = max(n_max,2)
    cabs = ["Data","Dia"]
    for i in range(1,n_max//2+1): cabs += [f"E{i}",f"S{i}"]
    cabs += ["H.Trab.","H.Extra","Observação"]
    for ci,t in enumerate(cabs,1):
        c = ws.cell(1,ci,t); _cab(c)
        ws.column_dimensions[get_column_letter(ci)].width = 12
    for li,reg in enumerate(regs,2):
        ws.cell(li,1,reg.get("data","")); ws.cell(li,2,reg.get("dia_semana",""))
        for ci,hs in enumerate(reg.get("entradas_saidas",[]),3):
            ws.cell(li,ci,hs)
        ultimo = 2+n_max
        ws.cell(li,ultimo+1,reg.get("horas_trabalhadas",""))
        ws.cell(li,ultimo+2,reg.get("horas_extras",""))
        ws.cell(li,ultimo+3,reg.get("observacao",""))
    ws.freeze_panes = "A2"

def gerar_excel(dados, ficha, ponto, inc_pag, inc_pto) -> bytes:
    wb = Workbook()
    ws = wb.active; ws.title = "LIQUIDACAO"
    aba_liquidacao_xl(ws, dados)
    if inc_pag: aba_pagamentos_xl(wb.create_sheet("PAGAMENTOS"), ficha)
    if inc_pto: aba_ponto_xl(wb.create_sheet("PONTO"), ponto)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue()


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
dados = decisoes_lista = criterios = ficha = ponto = None

with st.spinner("Lendo PDF..."):
    doc_fitz, capa, txt, toc, npags = ler_pdf(pdf_bytes)
    secs = buscar_secoes(doc_fitz, toc, txt)

secoes_ok = list(secs.keys())
st.write(f"✅ {npags} páginas lidas" +
         (f" — seções: {', '.join(secoes_ok)}" if secoes_ok else " — nenhuma seção localizada por palavra-chave"))

if f_dados:
    with st.spinner("Extraindo dados do processo (regex)..."):
        dados = extrair_dados(txt, capa)
    num = dados.get("numero_processo","?")
    rec = dados.get("reclamante","?")
    emp = dados.get("reclamada_1","?")
    st.write(f"✅ Processo {num} — {rec} × {emp}")

if f_decisoes:
    trecho = "\n\n".join(filter(None, [secs.get("sentenca"), secs.get("acordao")]))
    if not trecho:
        trecho = txt[-50000:]  # fallback: final do processo
        st.caption("  → Sentença não localizada por palavra-chave. Usando final do documento.")
    with st.spinner("Extraindo decisões (texto literal)..."):
        decisoes_lista = extrair_decisoes(trecho)
    n = len(decisoes_lista)
    st.write(f"✅ {n} decisão(ões) extraída(s)" if n else "⚠️ Nenhuma decisão localizada")

if f_criterios:
    dispositivo = secs.get("dispositivo") or secs.get("sentenca") or txt[-30000:]
    with st.spinner("Extraindo critérios de liquidação (⚡IA — 1 chamada)..."):
        criterios = ext_criterios(dispositivo)
    if "erro" in criterios:
        st.error(f"❌ Critérios: {criterios['erro']}")
    else:
        st.write("✅ Critérios extraídos")

if f_ficha:
    with st.spinner("Extraindo ficha financeira (tabelas PyMuPDF)..."):
        ficha = extrair_ficha(doc_fitz)
    if "erro" in ficha:
        st.warning(f"⚠️ Ficha: {ficha['erro']}")
    else:
        n = len(ficha.get("competencias",[]))
        st.write(f"✅ Ficha: {n} competência(s), {len(ficha.get('rubricas',[]))} rubrica(s)")

if f_ponto:
    with st.spinner("Extraindo espelho de ponto (tabelas PyMuPDF)..."):
        ponto = extrair_ponto(doc_fitz)
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
        wb = gerar_word(dados or {}, decisoes_lista or [], criterios or {})
    st.download_button("📄 Word (.docx)", wb, f"sintese_{num}.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

if o_md:
    with st.spinner("Gerando Markdown..."):
        md = gerar_markdown(dados or {}, decisoes_lista or [], criterios or {})
    st.download_button("📝 Markdown (.md)", md.encode(), f"sintese_{num}.md","text/markdown")

if o_xl:
    with st.spinner("Gerando Excel..."):
        xl = gerar_excel(dados or {}, ficha or {}, ponto or {}, aba_pag, aba_pto)
    st.download_button("📊 Excel (.xlsx)", xl, f"liquidacao_{num}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
