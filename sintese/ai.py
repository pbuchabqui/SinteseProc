"""AI integration and criteria context assembly for SinteseProc."""

import json
import re
import time
from collections.abc import Callable
from html import escape

from groq import Groq

MODELO_RACIOCINIO = "openai/gpt-oss-120b"

# ── Base de conhecimento trabalhista (de references/trabalhista.md do ContextAI) ──
CONHECIMENTO_TRABALHISTA = """
## Critérios de liquidação frequentes

### Horas extras — bancários
- Divisor 180: bancário comum (art. 224, caput, CLT).
- Divisor 220: cargo de confiança (art. 224, §2º, CLT).
- Base de cálculo: salário + todas as verbas de natureza salarial — Súmula 264/TST.
- Adicional: o percentual convencional (CCT) prevalece sobre o legal.

### Reflexos (incidências)
RSR/sábados/feriados | férias + 1/3 | 13º salário | FGTS (sem multa 40% se contrato vigente) | aviso prévio (apenas em rescisão).

### Tema 9/TST — IRR 0010169-57.2013.5.05.0024
- RSR majorado por horas extras repercute em férias, 13º e FGTS APENAS a partir de 20/03/2023.
- Período anterior: OJ 394/SBDI-1/TST (não repercute).
- Sempre dividir o cálculo nesses dois marcos temporais.

### Atualização monetária (pós-ADC 58/STF)
- IPCA-E até 31/12/2021.
- SELIC simples a partir de 01/01/2022 (juros + correção unificados).
- NÃO duplicar juros e correção no mesmo período.

### Jurisprudência de referência
- Bancário cargo de confiança: Art. 224 §2º CLT; Súmula 102/TST
- Gratificação de função: Súmula 109/TST
- Divisor bancário: Súmula 124/TST
- Base de cálculo HE: Súmula 264/TST
- RSR e HE: OJ 394/SBDI-1; IRR Tema 9/TST
- Atualização monetária: ADC 58/STF; Súmula 381/TST
- INSS sobre parcelas: Súmula 368/TST (apurado mês a mês sobre valor histórico)
- IRPF: Art. 12-A Lei 7.713/1988 (tabela progressiva acumulada)

### INSS — Súmula 26/TRT-4
Descontos apurados mês a mês sobre o valor histórico, com exclusão dos juros de mora,
respeitado o limite máximo mensal do salário de contribuição, observadas as alíquotas
vigentes à época e os valores já recolhidos.
"""

# ── Regras absolutas de análise pericial (de instrucoes-analise.md do ContextAI) ──
REGRAS_ANALISE = """
## Regras absolutas
- Responda sempre em português brasileiro formal.
- Seja preciso com valores monetários e datas (use vírgula decimal, R$).
- Se o contexto for insuficiente, diga qual informação falta — NUNCA invente dados.
- Baseie-se apenas no conteúdo fornecido do processo.
- Cite súmulas, OJs, artigos de lei e teses quando aplicáveis.
- Documentação incompleta: diga claramente e oriente como proceder.
"""


SecretGetter = Callable[[str], str | None]
WarningCallback = Callable[[str], None]


TIPOS_CRITERIOS = {"sentenca", "acordao", "embargos", "decisao"}
TIPOS_ALERTAS = {"sentenca", "acordao", "embargos", "decisao", "ponto", "ficha", "calculos", "sumario"}
PADROES_RELEVANCIA_CRITERIOS = re.compile(
    r"horas extras|adicional|reflexos?|FGTS|INSS|IRRF|corre[çc][aã]o|SELIC|IPCA|"
    r"juros|divisor|base de c[áa]lculo|liquida[çc][aã]o|condeno|julgo",
    re.IGNORECASE,
)
PADROES_RELEVANCIA_ALERTAS = re.compile(
    r"per[íi]cia|perito|nomea[çc][aã]o|prazo|PJe-Calc|quesitos?|documenta[çc][aã]o|"
    r"cart[aã]o de ponto|holerite|ficha financeira|c[áa]lculo|liquida[çc][aã]o|"
    r"impugna[çc][aã]o|diverg[êe]ncia|honor[áa]rios",
    re.IGNORECASE,
)


def _trim_to_limit(text: str, limite: int) -> str:
    if len(text) <= limite:
        return text
    aviso = "[CONTEXTO TRUNCADO: foram preservadas a sentença e as decisões mais recentes dentro do limite disponível.]\n\n"
    return aviso + text[-max(limite - len(aviso), 0):]


def _formatar_decisao_para_contexto(decisao: dict, indice: int) -> str:
    verbas = decisao.get("verbas_deferidas") or []
    partes = [
        f"## Decisão {indice}: {decisao.get('tipo', 'Decisão')}",
        f"Data: {decisao.get('data') or 'não localizada'}",
        f"Resultado: {decisao.get('resultado_reclamante') or 'não classificado'}",
    ]
    if decisao.get("id_documento"):
        partes.append(f"ID do documento: {decisao['id_documento']}")
    if decisao.get("pagina_inicial"):
        pagina_final = decisao.get("pagina_final") or decisao.get("pagina_inicial")
        partes.append(f"Páginas: {decisao.get('pagina_inicial')} a {pagina_final}")
    if verbas:
        partes.append("Verbas identificadas: " + ", ".join(verbas))
    partes.append("Dispositivo:\n" + (decisao.get("dispositivo") or "(dispositivo não localizado)"))
    return "\n".join(partes)


def _normalizar_fingerprint(texto: str) -> str:
    return re.sub(r"\W+", "", (texto or "").lower())[:1000]


def _recortar_texto(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    metade = max((limite - 80) // 2, 0)
    return (
        texto[:metade].rstrip()
        + "\n[... TRECHO INTERMEDIÁRIO OMITIDO PARA CABER NO ORÇAMENTO ...]\n"
        + texto[-metade:].lstrip()
    )[:limite]


def _extrair_texto_pagina(texto_completo: str, pagina: int, limite: int = 3500) -> str:
    if not texto_completo or not pagina:
        return ""
    padrao = re.compile(
        rf"\[PÁGINA {pagina}\]\n(.*?)(?=\n\[PÁGINA \d+\]\n|\Z)",
        re.DOTALL,
    )
    m = padrao.search(texto_completo)
    return _recortar_texto(m.group(1), limite) if m else ""


def _novo_bloco_contexto(
    tipo: str,
    prioridade: int,
    texto: str,
    fonte: str,
    titulo: str = "",
    paginas: str = "",
    confianca: str = "",
    motivo: str = "",
    source_id: str = "",
    titulo_origem: str = "",
    ocr_aplicado: bool = False,
) -> dict:
    return {
        "tipo": tipo,
        "prioridade": prioridade,
        "texto": (texto or "").strip(),
        "fonte": fonte,
        "titulo": titulo or tipo.capitalize(),
        "paginas": paginas or "não informada",
        "confianca": confianca or "não informada",
        "motivo": motivo or "relevância documental",
        "source_id": source_id or "",
        "titulo_origem": titulo_origem or "",
        "ocr_aplicado": bool(ocr_aplicado),
    }


def _blocos_decisoes(decisoes: list[dict]) -> list[dict]:
    blocos = []
    for indice, decisao in enumerate(decisoes or [], 1):
        tipo_label = decisao.get("tipo", "Decisão")
        tipo_norm = tipo_label.lower()
        if "senten" in tipo_norm:
            tipo = "sentenca"
            prioridade = 10
        elif "acórd" in tipo_norm or "acord" in tipo_norm:
            tipo = "acordao"
            prioridade = 20 + indice
        elif "embargo" in tipo_norm:
            tipo = "embargos"
            prioridade = 25 + indice
        else:
            tipo = "decisao"
            prioridade = 35 + indice
        texto = _formatar_decisao_para_contexto(decisao, indice)
        blocos.append(_novo_bloco_contexto(
            tipo=tipo,
            prioridade=prioridade,
            texto=texto,
            fonte="decisao_extraida",
            titulo=f"Decisão {indice}: {tipo_label}",
            paginas=(
                f"{decisao.get('pagina_inicial')} a {decisao.get('pagina_final') or decisao.get('pagina_inicial')}"
                if decisao.get("pagina_inicial") else ""
            ),
            confianca="ALTO",
            motivo="dispositivo e metadados extraídos deterministicamente",
            source_id=decisao.get("id_documento") or "",
            titulo_origem=decisao.get("titulo_origem") or "",
        ))
    return blocos


def _blocos_secoes(secs: dict, objetivo: str) -> list[dict]:
    prioridades = {
        "sentenca": 12,
        "acordao": 22,
        "embargos": 28,
        "decisao": 36,
        "ficha": 60,
        "ponto": 62,
        "calculos": 64,
        "sumario": 70,
    }
    tipos = TIPOS_CRITERIOS if objetivo == "criterios" else TIPOS_ALERTAS
    blocos = []
    for tipo in prioridades:
        if tipo not in tipos:
            continue
        texto = secs.get(tipo)
        if not texto:
            continue
        blocos.append(_novo_bloco_contexto(
            tipo=tipo,
            prioridade=prioridades[tipo],
            texto=texto,
            fonte="secao_detectada",
            titulo=f"Seção detectada: {tipo}",
            motivo="fallback determinístico de seção textual",
        ))
    return blocos


def _blocos_estrutura_pdf(estrutura_pdf: dict | None, texto_completo: str, objetivo: str) -> list[dict]:
    if not estrutura_pdf:
        return []
    blocos = []
    classificacao = estrutura_pdf.get("classificacao_tecnica", {})
    auditoria = estrutura_pdf.get("auditoria", {})
    alertas = estrutura_pdf.get("alertas") or []
    resumo = [
        "Classificação técnica do PDF:",
        f"- Tipo: {classificacao.get('tipo_pdf') or 'não informado'}",
        f"- Confiança global: {classificacao.get('confianca_global') or 'não informada'}",
        f"- Necessita OCR: {'sim' if classificacao.get('necessita_ocr') else 'não'}",
        f"- Páginas OCRizadas: {auditoria.get('paginas_ocr', 0)}",
        f"- Páginas candidatas a decisões: {auditoria.get('paginas_candidatas_decisoes', 0)}",
        f"- Páginas candidatas a ponto: {auditoria.get('paginas_candidatas_ponto', 0)}",
        f"- Páginas candidatas a holerites: {auditoria.get('paginas_candidatas_holerites', 0)}",
        f"- Páginas candidatas a cálculos: {auditoria.get('paginas_candidatas_calculos', 0)}",
    ]
    if alertas:
        resumo.append("Alertas técnicos: " + " | ".join(alertas))
    blocos.append(_novo_bloco_contexto(
        tipo="preprocessamento",
        prioridade=5 if objetivo == "alertas" else 80,
        texto="\n".join(resumo),
        fonte="estrutura_pdf",
        titulo="Auditoria técnica do PDF",
        confianca=classificacao.get("confianca_global") or "",
        motivo="rastreabilidade e qualidade da extração",
    ))

    if objetivo == "criterios":
        tipos_aceitos = {"decisao"}
    else:
        tipos_aceitos = {"decisao", "ponto", "holerite", "calculos", "sumario"}

    for pagina in estrutura_pdf.get("paginas") or []:
        possiveis = set(pagina.get("possiveis_tipos") or [])
        incluir_por_alerta = (
            objetivo == "alertas"
            and (
                pagina.get("ocr_aplicado")
                or pagina.get("confianca") in {"BAIXO", "CRÍTICO"}
                or pagina.get("alertas")
            )
        )
        if not (possiveis & tipos_aceitos) and not incluir_por_alerta:
            continue
        pagina_pdf = pagina.get("pagina_pdf")
        texto_pagina = _extrair_texto_pagina(texto_completo, pagina_pdf)
        if not texto_pagina and not pagina.get("alertas"):
            continue
        tipo = sorted(possiveis & tipos_aceitos)[0] if possiveis & tipos_aceitos else "pagina_alerta"
        prioridade = {
            "decisao": 38,
            "calculos": 48,
            "ponto": 52,
            "holerite": 54,
            "sumario": 68,
            "pagina_alerta": 46,
        }.get(tipo, 75)
        texto = texto_pagina or "Alertas da página: " + " | ".join(pagina.get("alertas") or [])
        blocos.append(_novo_bloco_contexto(
            tipo=tipo,
            prioridade=prioridade,
            texto=texto,
            fonte="pagina_pdf",
            titulo=f"Página candidata: {tipo}",
            paginas=str(pagina_pdf),
            confianca=pagina.get("confianca") or "",
            motivo="página candidata identificada no pré-processamento",
            source_id=f"pagina-{pagina_pdf}",
            ocr_aplicado=pagina.get("ocr_aplicado", False),
        ))
    return blocos


def _blocos_por_relevancia_textual(texto_completo: str, objetivo: str) -> list[dict]:
    if not texto_completo:
        return []
    padrao = PADROES_RELEVANCIA_CRITERIOS if objetivo == "criterios" else PADROES_RELEVANCIA_ALERTAS
    blocos = []
    for m in padrao.finditer(texto_completo):
        inicio = max(m.start() - 900, 0)
        fim = min(m.end() + 1800, len(texto_completo))
        trecho = texto_completo[inicio:fim]
        pagina_m = re.findall(r"\[PÁGINA (\d+)\]", texto_completo[max(0, inicio - 80):m.start()])
        pagina = pagina_m[-1] if pagina_m else ""
        blocos.append(_novo_bloco_contexto(
            tipo="trecho_relevante",
            prioridade=78 + len(blocos),
            texto=trecho,
            fonte="busca_textual",
            titulo=f"Trecho por palavra-chave: {m.group(0)}",
            paginas=pagina,
            motivo="termo relevante localizado no texto completo",
        ))
        if len(blocos) >= 8:
            break
    return blocos


def _deduplicar_blocos(blocos: list[dict]) -> list[dict]:
    vistos = set()
    textos_vistos = []
    unicos = []
    for bloco in sorted(blocos, key=lambda b: (b["prioridade"], b["tipo"], b["titulo"])):
        texto = bloco.get("texto", "")
        fp = _normalizar_fingerprint(texto)
        if not fp or fp in vistos:
            continue
        if len(fp) > 120 and any(fp in visto or visto in fp for visto in textos_vistos if len(visto) > 120):
            continue
        vistos.add(fp)
        textos_vistos.append(fp)
        unicos.append(bloco)
    return unicos


def _formatar_mapa_evidencias(blocos: list[dict]) -> str:
    return formatar_evidence_index(blocos)


def _xml_attr(valor) -> str:
    return escape(str(valor or ""), quote=True)


def _xml_text(valor) -> str:
    return escape(str(valor or ""), quote=False)


def formatar_evidence_index(blocos: list[dict]) -> str:
    linhas = ["<evidence_index>"]
    for idx, bloco in enumerate(blocos, 1):
        linhas.append(
            "  "
            f"<item id=\"EV-{idx:02d}\" "
            f"type=\"{_xml_attr(bloco['tipo'])}\" "
            f"source=\"{_xml_attr(bloco['fonte'])}\" "
            f"source_id=\"{_xml_attr(bloco.get('source_id'))}\" "
            f"pages=\"{_xml_attr(bloco['paginas'])}\" "
            f"confidence=\"{_xml_attr(bloco['confianca'])}\" "
            f"ocr=\"{'true' if bloco.get('ocr_aplicado') else 'false'}\" "
            f"title=\"{_xml_attr(bloco['titulo'])}\" />"
        )
    linhas.append("</evidence_index>")
    return "\n".join(linhas)


def formatar_evidence_xml(bloco: dict, idx: int, limite_texto: int = 4500) -> str:
    texto = _recortar_texto(bloco["texto"], limite_texto)
    return "\n".join([
        f"<evidence id=\"EV-{idx:02d}\" "
        f"type=\"{_xml_attr(bloco['tipo'])}\" "
        f"source=\"{_xml_attr(bloco['fonte'])}\" "
        f"source_id=\"{_xml_attr(bloco.get('source_id'))}\" "
        f"pages=\"{_xml_attr(bloco['paginas'])}\" "
        f"confidence=\"{_xml_attr(bloco['confianca'])}\" "
        f"ocr=\"{'true' if bloco.get('ocr_aplicado') else 'false'}\">",
        f"  <title>{_xml_text(bloco['titulo'])}</title>",
        f"  <origin_title>{_xml_text(bloco.get('titulo_origem'))}</origin_title>",
        f"  <reason>{_xml_text(bloco['motivo'])}</reason>",
        "  <content>",
        _xml_text(texto),
        "  </content>",
        "</evidence>",
    ])


def _formatar_bloco_evidencia(bloco: dict, idx: int, limite_texto: int) -> str:
    return formatar_evidence_xml(bloco, idx, limite_texto)


def formatar_critical_recap(blocos: list[dict], objetivo: str) -> str:
    tipos_criticos = {"sentenca", "acordao", "embargos", "decisao"}
    criticos = [
        (idx, bloco)
        for idx, bloco in enumerate(blocos, 1)
        if (
            bloco["tipo"] in tipos_criticos
            or bloco.get("ocr_aplicado")
            or bloco.get("confianca") in {"BAIXO", "CRÍTICO"}
        )
    ]
    if not criticos:
        criticos = list(enumerate(blocos[:3], 1))

    linhas = [
        f"<critical_recap objective=\"{_xml_attr(objetivo)}\">",
        "  <instruction>Releia estes pontos antes de responder; eles reduzem perda de informação no meio do contexto.</instruction>",
    ]
    for idx, bloco in criticos[:6]:
        linhas.extend([
            f"  <recap evidence_id=\"EV-{idx:02d}\" "
            f"type=\"{_xml_attr(bloco['tipo'])}\" "
            f"pages=\"{_xml_attr(bloco['paginas'])}\" "
            f"confidence=\"{_xml_attr(bloco['confianca'])}\" "
            f"source_id=\"{_xml_attr(bloco.get('source_id'))}\" "
            f"ocr=\"{'true' if bloco.get('ocr_aplicado') else 'false'}\">",
            f"    <title>{_xml_text(bloco['titulo'])}</title>",
            f"    <snippet>{_xml_text(_recortar_texto(bloco['texto'], 650))}</snippet>",
            "  </recap>",
        ])
    linhas.append("</critical_recap>")
    return "\n".join(linhas)


def montar_prompt_tarefa(instrucao: str, contexto: str) -> str:
    contexto = (contexto or "").strip()
    if "<document_context" not in contexto:
        contexto = "\n".join([
            "<document_context format=\"raw_fallback\">",
            "  <evidence id=\"EV-RAW\" type=\"raw_text\" source=\"fallback\" confidence=\"não informada\">",
            "    <content>",
            _xml_text(contexto),
            "    </content>",
            "  </evidence>",
            "</document_context>",
        ])
    return "\n\n".join([
        contexto,
        "<task_context>",
        _xml_text((instrucao or "").strip()),
        "</task_context>",
    ])


def selecionar_blocos_contexto(blocos: list[dict], limite: int) -> tuple[list[dict], bool]:
    """Seleciona blocos determinística e deduplicadamente dentro de um orçamento aproximado."""
    selecionados = []
    usado = 0
    truncado = False
    for bloco in _deduplicar_blocos(blocos):
        custo = min(len(bloco["texto"]), 4500) + 380
        if usado + custo <= limite or not selecionados:
            selecionados.append(bloco)
            usado += custo
        else:
            truncado = True
    return selecionados, truncado


def montar_contexto_longo(
    decisoes: list[dict] | None,
    secs: dict | None,
    texto_completo: str,
    estrutura_pdf: dict | None = None,
    objetivo: str = "criterios",
    limite: int = 30000,
) -> str:
    """Monta contexto IA com evidências priorizadas, orçamento e mitigação lost-in-the-middle."""
    decisoes = decisoes or []
    secs = secs or {}
    objetivo = objetivo if objetivo in {"criterios", "alertas"} else "criterios"

    blocos = []
    blocos.extend(_blocos_decisoes(decisoes))
    blocos.extend(_blocos_secoes(secs, objetivo))
    blocos.extend(_blocos_estrutura_pdf(estrutura_pdf, texto_completo, objetivo))
    blocos.extend(_blocos_por_relevancia_textual(texto_completo, objetivo))

    if not blocos:
        return (texto_completo or "")[-limite:]

    orcamento_blocos = max(limite - 4500, int(limite * 0.65))
    selecionados, truncado = selecionar_blocos_contexto(blocos, orcamento_blocos)

    cabecalho = [
        f"<document_context objective=\"{_xml_attr(objetivo)}\" format=\"structured_evidence_v1\">",
        "  <context_rules>",
        "    Use apenas as evidências abaixo. Se uma informação não estiver nas evidências, marque como ausente ou pendente de conferência.",
        "    Cada evidência contém fonte, páginas, OCR e confiança técnica quando disponíveis.",
        "  </context_rules>",
        _formatar_mapa_evidencias(selecionados),
        "<evidences>",
    ]
    limite_por_bloco = max(1200, min(4500, orcamento_blocos // max(len(selecionados), 1)))
    corpo = [
        _formatar_bloco_evidencia(bloco, idx, limite_por_bloco)
        for idx, bloco in enumerate(selecionados, 1)
    ]
    fechamento = [
        "</evidences>",
        formatar_critical_recap(selecionados, objetivo),
    ]
    if truncado:
        fechamento.append("<truncation_notice>CONTEXTO TRUNCADO: blocos menos relevantes foram omitidos para preservar o orçamento.</truncation_notice>")
    fechamento.append("</document_context>")

    contexto = "\n\n".join(cabecalho + corpo + fechamento)
    if len(contexto) <= limite:
        return contexto
    aviso = "<truncation_notice>CONTEXTO TRUNCADO: evidências críticas preservadas no início e no critical_recap.</truncation_notice>\n"
    return aviso + _recortar_texto(contexto, max(limite - len(aviso), 0))


def montar_contexto_criterios(
    decisoes: list[dict] | None,
    secs: dict | None,
    texto_completo: str,
    limite: int = 30000,
    estrutura_pdf: dict | None = None,
) -> str:
    """Monta o texto enviado à IA para critérios de liquidação.

    Prioriza todos os dispositivos extraídos em ordem. Quando não há decisões,
    usa seções determinísticas e, por último, o fim do texto completo.
    """
    decisoes = decisoes or []
    secs = secs or {}

    if estrutura_pdf:
        return montar_contexto_longo(
            decisoes,
            secs,
            texto_completo,
            estrutura_pdf=estrutura_pdf,
            objetivo="criterios",
            limite=limite,
        )

    if decisoes:
        blocos = [_formatar_decisao_para_contexto(decisao, i) for i, decisao in enumerate(decisoes, 1)]
        contexto = "\n\n---\n\n".join(blocos)
        if len(contexto) <= limite:
            return contexto

        sentencas = [b for b in blocos if "## Decisão" in b and "Sentença" in b]
        recentes = list(reversed(blocos))
        selecionados: list[str] = []
        usados = set()
        for bloco in sentencas + recentes:
            key = id(bloco)
            if key in usados:
                continue
            usados.add(key)
            candidato = "\n\n---\n\n".join(selecionados + [bloco])
            if len(candidato) <= limite:
                selecionados.append(bloco)
            elif not selecionados:
                selecionados.append(_trim_to_limit(bloco, limite))
                break
        return _trim_to_limit("\n\n---\n\n".join(selecionados), limite)

    fallback = "\n\n".join(filter(None, [
        secs.get("sentenca"),
        secs.get("acordao"),
        secs.get("embargos"),
        secs.get("decisao"),
    ]))
    if fallback:
        return _trim_to_limit(fallback, limite)
    return (texto_completo or "")[-limite:]


def chamar_ia(
    txt: str,
    instrucao: str,
    get_secret: SecretGetter,
    groq_key: str,
    limite: int = 30000,
    incluir_base_trabalhista: bool = False,
    warning_callback: WarningCallback | None = None,
) -> dict:
    """Chamada ao Groq com retry automático em caso de rate limit."""
    modelo = get_secret("GROQ_MODEL") or "llama-3.3-70b-versatile"
    usa_raciocinio = modelo == MODELO_RACIOCINIO

    base = CONHECIMENTO_TRABALHISTA if incluir_base_trabalhista else ""
    contexto_orcado = _recortar_texto(txt, max(limite - len(instrucao) - 300, 1000))
    prompt_usuario = montar_prompt_tarefa(instrucao, contexto_orcado)
    if len(prompt_usuario) > limite:
        contexto_orcado = _recortar_texto(txt, max(limite - len(instrucao) - 900, 1000))
        prompt_usuario = montar_prompt_tarefa(instrucao, contexto_orcado)

    params = dict(
        model=modelo, stream=False,
        messages=[
            {"role":"system","content":(
                "Você é perito contábil trabalhista brasileiro especializado em liquidação de sentença no TRT4.\n"
                + REGRAS_ANALISE
                + (f"\n\n## Base de referência jurídica\n{base}" if base else "")
                + "\n\nResponda APENAS com JSON válido, sem texto antes/depois, sem ```json```.")},
            {"role":"user","content": prompt_usuario},
        ]
    )
    if usa_raciocinio:
        params.update({"max_completion_tokens":3000,"temperature":0.6,"top_p":1,
                       "reasoning_effort": get_secret("GROQ_REASONING_EFFORT") or "medium"})
    else:
        params.update({"max_tokens":3000,"temperature":0.1})

    for tentativa in range(3):
        try:
            r = Groq(api_key=groq_key).chat.completions.create(**params)
            raw = r.choices[0].message.content
            try:
                return json.loads(re.sub(r"```(?:json)?|```","",raw).strip())
            except json.JSONDecodeError:
                return {"erro": "JSON inválido", "texto_bruto": raw}

        except Exception as e:
            msg = str(e)
            if "rate_limit" in msg.lower() or "429" in msg:
                if tentativa < 2:
                    espera = 65 if tentativa == 0 else 90
                    if warning_callback:
                        warning_callback(f"⏳ Rate limit — aguardando {espera}s (tentativa {tentativa+1}/3)...")
                    time.sleep(espera)
                    continue
                return {"erro": "Rate limit persistente. Tente novamente em alguns minutos."}
            return {"erro": f"Erro na API: {msg}"}

    return {"erro": "Falha após 3 tentativas."}

def ext_criterios(dispositivo: str, get_secret: SecretGetter, groq_key: str, warning_callback: WarningCallback | None = None) -> dict:
    """
    Única chamada de IA no sistema.
    Interpreta o dispositivo da sentença para extrair parâmetros de liquidação.
    Isso exige compreensão semântica — regex não resolve.
    """
    return chamar_ia(dispositivo, """
Analise o dispositivo da sentença/acórdão trabalhista e extraia os critérios de liquidação.
Use a base de referência jurídica fornecida para preencher critérios não explicitados na sentença
(ex: se não há índice de correção, aplicar SELIC por ADC 58; se não há divisor, usar o padrão da categoria).
O texto pode vir em formato de pacote de contexto com Mapa de Evidências. Use apenas essas evidências;
quando um critério não tiver fonte no pacote, indique a ausência em "observacoes".

{
  "criterios": {
    "base_salarial": "descrição da base (ex: última remuneração R$ X)",
    "periodo_apurado": {"inicio": "dd/mm/aaaa", "fim": "dd/mm/aaaa"},
    "jornada_contratual": "ex: 8h diárias / 44h semanais",
    "jornada_real_apurada": "ex: 10h diárias conforme cartões de ponto",
    "divisor": "220 ou 180 ou outro — justificar",
    "adicional_horas_extras": "50% ou 100% ou percentual CCT",
    "reflexos": ["DSR", "férias", "13º salário", "aviso prévio", "FGTS"],
    "marco_tema9": "aplicar Tema 9/TST a partir de 20/03/2023 se houver RSR majorado",
    "fgts": {"base": "todas as verbas deferidas", "multa_40": true},
    "atualizacao_monetaria": "IPCA-E até 31/12/2021 + SELIC a partir de 01/01/2022 (ADC 58)",
    "juros": "incluídos na SELIC a partir de 01/01/2022",
    "inss_empregado": "Súmula 26/TRT-4: mês a mês sobre valor histórico",
    "inss_patronal": "a cargo da reclamada",
    "ir": "tabela progressiva acumulada (art. 12-A Lei 7.713/1988) ou isento",
    "exclusoes_expressas": ["ex: dano moral não integra base FGTS"],
    "observacoes": "outros critérios relevantes incluindo verbas deferidas e deduções autorizadas"
  }
}
""", get_secret=get_secret, groq_key=groq_key, limite=30000, incluir_base_trabalhista=True, warning_callback=warning_callback)


def ext_alertas_periciais(texto_processo: str, get_secret: SecretGetter, groq_key: str, warning_callback: WarningCallback | None = None) -> dict:
    """
    Gera a Seção 8 — Alertas Periciais (obrigatória conforme instrucoes-analise.md).
    Usa o texto das decisões + despacho de nomeação para identificar riscos e atenções.
    """
    return chamar_ia(texto_processo, """
Analise o processo trabalhista e gere os ALERTAS PERICIAIS para o perito.
Esta é a seção mais importante do relatório — seja específico e prático.
O texto pode vir em formato de pacote de contexto com Mapa de Evidências. Use apenas essas evidências;
quando a fonte for OCR ou tiver confiança baixa, indique necessidade de conferência humana.

{
  "alertas": {
    "formato_laudo": "PJe-Calc obrigatório | laudo livre | verificar despacho",
    "prazo_laudo": "data ou 'verificar despacho de nomeação'",
    "pje_calc_exigido": true,
    "documentacao_faltante": ["lista do que não está nos autos mas é necessário"],
    "pontos_atencao": [
      "alertas específicos do caso — riscos, armadilhas, divergências prováveis"
    ],
    "marcos_temporais_criticos": [
      "ex: contrato anterior a 20/03/2023 — verificar Tema 9/TST para RSR"
    ],
    "vincendas": "apurar parcelas vincendas até data-base do laudo (art. 899/CLT)",
    "honorarios_risco": "OJ 19/TRT-3: divergência significativa pode gerar condenação em honorários",
    "observacoes_finais": "outros alertas não cobertos acima"
  }
}
""", get_secret=get_secret, groq_key=groq_key, limite=20000, incluir_base_trabalhista=True, warning_callback=warning_callback)
