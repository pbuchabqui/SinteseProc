from sintese.ai import (
    extrair_json_resposta_manual,
    montar_contexto_criterios,
    montar_contexto_longo,
    montar_prompt_manual_alertas,
    montar_prompt_manual_criterios,
    montar_prompt_tarefa,
    selecionar_blocos_contexto,
    validar_resposta_manual,
)


def test_montar_contexto_criterios_inclui_sentenca_e_acordao_extraidos():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "10/04/2024",
            "id_documento": "abc12345",
            "pagina_inicial": 12,
            "pagina_final": 14,
            "resultado_reclamante": "parcialmente procedente",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "Dispositivo da sentença com horas extras.",
        },
        {
            "tipo": "Acórdão",
            "data": "20/08/2024",
            "resultado_reclamante": "negado provimento",
            "verbas_deferidas": ["FGTS"],
            "dispositivo": "Dispositivo do acórdão mantendo FGTS.",
        },
    ]

    contexto = montar_contexto_criterios(decisoes, {}, "texto completo irrelevante")

    assert "## Decisão 1: Sentença" in contexto
    assert "ID do documento: abc12345" in contexto
    assert "Páginas: 12 a 14" in contexto
    assert "Dispositivo da sentença com horas extras." in contexto
    assert "## Decisão 2: Acórdão" in contexto
    assert "Dispositivo do acórdão mantendo FGTS." in contexto
    assert "Verbas identificadas: horas extras" in contexto
    assert "Resultado: negado provimento" in contexto


def test_montar_contexto_criterios_usa_fallback_deterministico_sem_decisoes():
    secs = {
        "dispositivo": "texto antigo que não deve ser usado",
        "sentenca": "SENTENCA FALLBACK",
        "acordao": "ACORDAO FALLBACK",
        "embargos": "EMBARGOS FALLBACK",
        "decisao": "DECISAO FALLBACK",
    }

    contexto = montar_contexto_criterios([], secs, "TEXTO COMPLETO")

    assert "SENTENCA FALLBACK" in contexto
    assert "ACORDAO FALLBACK" in contexto
    assert "EMBARGOS FALLBACK" in contexto
    assert "DECISAO FALLBACK" in contexto
    assert "texto antigo que não deve ser usado" not in contexto


def test_montar_contexto_criterios_usa_final_do_texto_completo_no_ultimo_fallback():
    texto = "A" * 10 + "FINAL DO PROCESSO"

    contexto = montar_contexto_criterios(None, {}, texto, limite=12)

    assert contexto == " DO PROCESSO"


def test_montar_contexto_criterios_trunca_preservando_aviso():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "01/01/2024",
            "resultado_reclamante": "parcialmente procedente",
            "verbas_deferidas": [],
            "dispositivo": "S" * 200,
        },
        {
            "tipo": "Acórdão",
            "data": "01/02/2024",
            "resultado_reclamante": "parcialmente provido",
            "verbas_deferidas": [],
            "dispositivo": "A" * 200,
        },
    ]

    contexto = montar_contexto_criterios(decisoes, {}, "", limite=120)

    assert len(contexto) <= 120
    assert "CONTEXTO TRUNCADO" in contexto


def test_montar_contexto_longo_inclui_mapa_e_sentenca_acordao():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "10/04/2024",
            "id_documento": "sent12345",
            "titulo_origem": "1. 10/04/2024 - Sentença - sent12345",
            "pagina_inicial": 10,
            "pagina_final": 12,
            "resultado_reclamante": "parcialmente procedente",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "Sentença deferiu horas extras com reflexos em FGTS.",
        },
        {
            "tipo": "Acórdão",
            "data": "20/08/2024",
            "resultado_reclamante": "negado provimento",
            "verbas_deferidas": ["FGTS"],
            "dispositivo": "Acórdão manteve a condenação em FGTS.",
        },
    ]
    texto = "texto inicial irrelevante " * 500

    contexto = montar_contexto_longo(decisoes, {}, texto, objetivo="criterios", limite=8000)

    assert "<document_context" in contexto
    assert 'format="structured_evidence_v2"' in contexto
    assert "<context_brief" in contexto
    assert "<evidence_budget" in contexto
    assert "<source_priority_rules" in contexto
    assert "<context_probe" in contexto
    assert "<evidence_index>" in contexto
    assert '<evidence id="EV-01"' in contexto
    assert '<critical_recap objective="criterios">' in contexto
    assert 'source_id="sent12345"' in contexto
    assert 'pages="10 a 12"' in contexto
    assert 'confidence="ALTO"' in contexto
    assert "Sentença deferiu horas extras" in contexto
    assert "Acórdão manteve a condenação" in contexto


def test_montar_contexto_longo_inclui_ocr_pagina_e_confianca():
    estrutura_pdf = {
        "classificacao_tecnica": {"tipo_pdf": "misto", "confianca_global": "MÉDIO", "necessita_ocr": False},
        "auditoria": {"paginas_ocr": 1, "paginas_candidatas_decisoes": 1},
        "paginas": [
            {
                "pagina_pdf": 2,
                "ocr_aplicado": True,
                "confianca": "MÉDIO",
                "possiveis_tipos": ["decisao"],
                "alertas": ["texto obtido por OCR"],
            }
        ],
        "alertas": [],
    }
    texto = "[PÁGINA 1]\nCapa\n[PÁGINA 2]\nSENTENÇA OCRIZADA\nAnte o exposto, condeno ao pagamento de horas extras."

    contexto = montar_contexto_longo([], {}, texto, estrutura_pdf=estrutura_pdf, objetivo="criterios", limite=7000)

    assert "Página candidata: decisao" in contexto
    assert 'pages="2"' in contexto
    assert 'confidence="MÉDIO"' in contexto
    assert 'ocr="true"' in contexto
    assert "SENTENÇA OCRIZADA" in contexto


def test_montar_contexto_longo_trunca_com_reforco_final():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "01/01/2024",
            "resultado_reclamante": "procedente",
            "verbas_deferidas": [],
            "dispositivo": "SENTENÇA CRÍTICA " + ("S" * 6000),
        },
        {
            "tipo": "Acórdão",
            "data": "02/02/2024",
            "resultado_reclamante": "provido",
            "verbas_deferidas": [],
            "dispositivo": "ACÓRDÃO CRÍTICO " + ("A" * 6000),
        },
    ]

    contexto = montar_contexto_longo(decisoes, {}, "", objetivo="criterios", limite=3600)

    assert len(contexto) <= 3600
    assert "CONTEXTO TRUNCADO" in contexto
    assert "SENTENÇA CRÍTICA" in contexto
    assert "<critical_recap" in contexto or "critical_recap" in contexto


def test_selecionar_blocos_contexto_deduplica_trechos_repetidos():
    texto_repetido = "Dispositivo repetido sobre horas extras e FGTS. " * 8
    blocos = [
        {"tipo": "sentenca", "prioridade": 1, "texto": texto_repetido, "fonte": "toc", "titulo": "A", "paginas": "1", "confianca": "ALTO", "motivo": "x"},
        {"tipo": "sentenca", "prioridade": 2, "texto": texto_repetido, "fonte": "fallback", "titulo": "B", "paginas": "1", "confianca": "ALTO", "motivo": "x"},
    ]

    selecionados, truncado = selecionar_blocos_contexto(blocos, limite=5000)

    assert truncado is False
    assert len(selecionados) == 1
    assert selecionados[0]["fonte"] == "toc"


def test_contexto_alertas_usa_preprocessamento_e_candidatos_sem_txt_final():
    estrutura_pdf = {
        "classificacao_tecnica": {"tipo_pdf": "misto", "confianca_global": "MÉDIO", "necessita_ocr": False},
        "auditoria": {"paginas_ocr": 0, "paginas_candidatas_ponto": 1, "paginas_candidatas_holerites": 1},
        "paginas": [
            {"pagina_pdf": 3, "confianca": "ALTO", "possiveis_tipos": ["ponto"], "alertas": []},
            {"pagina_pdf": 4, "confianca": "ALTO", "possiveis_tipos": ["holerite"], "alertas": []},
        ],
        "alertas": ["verificar documentação faltante"],
    }
    texto = (
        "[PÁGINA 1]\nCapa\n"
        "[PÁGINA 3]\nCartão de ponto com marcações 08:00 12:00 13:00 18:00.\n"
        "[PÁGINA 4]\nHolerite com proventos e descontos.\n"
        + ("TEXTO FINAL IRRELEVANTE " * 500)
    )

    contexto = montar_contexto_longo([], {}, texto, estrutura_pdf=estrutura_pdf, objetivo="alertas", limite=8000)

    assert "Auditoria técnica do PDF" in contexto
    assert "Cartão de ponto com marcações" in contexto
    assert "Holerite com proventos" in contexto
    assert "verificar documentação faltante" in contexto


def test_contexto_alertas_inclui_pagina_ocr_baixa_confianca_sem_tipo():
    estrutura_pdf = {
        "classificacao_tecnica": {"tipo_pdf": "misto", "confianca_global": "BAIXO", "necessita_ocr": True},
        "auditoria": {"paginas_ocr": 1},
        "paginas": [
            {
                "pagina_pdf": 9,
                "confianca": "BAIXO",
                "ocr_aplicado": True,
                "possiveis_tipos": [],
                "alertas": ["texto obtido por OCR; conferir literalidade"],
            }
        ],
        "alertas": [],
    }
    texto = "[PÁGINA 9]\nTrecho OCRizado com baixa legibilidade e possível prazo pericial."

    contexto = montar_contexto_longo([], {}, texto, estrutura_pdf=estrutura_pdf, objetivo="alertas", limite=7000)

    assert 'type="pagina_alerta"' in contexto
    assert 'pages="9"' in contexto
    assert 'confidence="BAIXO"' in contexto
    assert 'ocr="true"' in contexto
    assert "Trecho OCRizado com baixa legibilidade" in contexto


def test_montar_prompt_tarefa_coloca_tarefa_apos_contexto():
    contexto = "<document_context><evidence id=\"EV-01\">conteúdo</evidence></document_context>"
    prompt = montar_prompt_tarefa("Extraia critérios.", contexto)

    assert prompt.index("<document_context") < prompt.index("<task_context>")
    assert prompt.rstrip().endswith("</task_context>")
    assert "Extraia critérios." in prompt


def test_montar_contexto_criterios_com_estrutura_pdf_usa_contexto_estruturado():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "10/04/2024",
            "id_documento": "abc12345",
            "pagina_inicial": 12,
            "pagina_final": 14,
            "resultado_reclamante": "parcialmente procedente",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "Dispositivo da sentença com horas extras.",
        }
    ]
    estrutura_pdf = {
        "classificacao_tecnica": {"tipo_pdf": "nativo", "confianca_global": "ALTO", "necessita_ocr": False},
        "auditoria": {},
        "paginas": [],
        "alertas": [],
    }

    contexto = montar_contexto_criterios(decisoes, {}, "texto completo", estrutura_pdf=estrutura_pdf)

    assert "<document_context" in contexto
    assert 'format="structured_evidence_v2"' in contexto
    assert "<evidence_index>" in contexto
    assert 'source_id="abc12345"' in contexto


def test_contexto_v2_recap_e_probe_reforcam_sentenca_acordao_com_texto_enorme():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "01/03/2024",
            "resultado_reclamante": "procedente em parte",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "SENTENÇA ESSENCIAL deferindo horas extras e reflexos.",
        },
        {
            "tipo": "Acórdão",
            "data": "20/07/2024",
            "resultado_reclamante": "negado provimento",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "ACÓRDÃO ESSENCIAL mantendo horas extras.",
        },
    ]
    texto = ("ruído processual " * 2000) + "base de cálculo divisor FGTS"

    contexto = montar_contexto_longo(decisoes, {}, texto, objetivo="criterios", limite=9000)

    assert "SENTENÇA ESSENCIAL" in contexto
    assert "ACÓRDÃO ESSENCIAL" in contexto
    assert contexto.index("<context_brief") < contexto.index("<evidences>")
    assert "Verificar sentença, acórdão e embargos" in contexto
    assert "<critical_recap" in contexto


def test_contexto_v2_deduplica_busca_textual_coberta_por_decisao():
    decisoes = [{
        "tipo": "Sentença",
        "data": "01/01/2024",
        "resultado_reclamante": "procedente",
        "verbas_deferidas": ["horas extras"],
        "dispositivo": "Condeno ao pagamento de horas extras com reflexos em FGTS.",
    }]
    texto = (
        "[PÁGINA 1]\nCondeno ao pagamento de horas extras com reflexos em FGTS.\n"
        "Trecho repetido sem novidade documental.\n"
    )

    contexto = montar_contexto_longo(decisoes, {}, texto, objetivo="criterios", limite=7000)

    assert 'source="decisao_extraida"' in contexto
    assert 'source="busca_textual"' not in contexto


def test_contexto_v2_orcamento_por_objetivo_prioriza_alertas_pdf_e_paginas():
    estrutura_pdf = {
        "classificacao_tecnica": {"tipo_pdf": "misto", "confianca_global": "BAIXO", "necessita_ocr": True},
        "auditoria": {"paginas_ocr": 1, "paginas_candidatas_calculos": 1, "paginas_candidatas_ponto": 1},
        "paginas": [
            {"pagina_pdf": 2, "confianca": "ALTO", "possiveis_tipos": ["calculos"], "alertas": []},
            {"pagina_pdf": 3, "confianca": "BAIXO", "ocr_aplicado": True, "possiveis_tipos": ["ponto"], "alertas": ["OCR baixo"]},
        ],
        "alertas": ["conferir OCR antes do laudo"],
    }
    texto = (
        "[PÁGINA 2]\nPlanilha de cálculos com PJe-Calc e diferenças.\n"
        "[PÁGINA 3]\nCartão de ponto OCRizado com marcações ilegíveis.\n"
    )

    contexto = montar_contexto_longo([], {}, texto, estrutura_pdf=estrutura_pdf, objetivo="alertas", limite=8000)

    assert 'objective="alertas"' in contexto
    assert "Auditoria técnica do PDF" in contexto
    assert 'type="calculos"' in contexto
    assert 'type="ponto"' in contexto
    assert 'confidence="BAIXO"' in contexto
    assert "Há OCR ou baixa confiança" in contexto


def test_contexto_v2_truncamento_informa_blocos_omitidos():
    decisoes = [{
        "tipo": "Sentença",
        "data": "01/01/2024",
        "resultado_reclamante": "procedente",
        "verbas_deferidas": [],
        "dispositivo": "SENTENÇA CRÍTICA " + ("S" * 3000),
    }]
    secs = {f"decisao": "DECISÃO SECUNDÁRIA " + ("D" * 2500)}
    texto = "horas extras FGTS reflexos divisor base de cálculo " * 1000

    contexto = montar_contexto_longo(decisoes, secs, texto, objetivo="criterios", limite=4200)

    assert "omitted_blocks" in contexto
    assert "truncation_reason" in contexto
    assert "CONTEXTO TRUNCADO" in contexto


def test_montar_prompt_manual_criterios_preserva_contexto_e_tarefa_no_fim():
    contexto = montar_contexto_longo(
        [{
            "tipo": "Sentença",
            "data": "10/04/2024",
            "resultado_reclamante": "procedente",
            "verbas_deferidas": ["horas extras"],
            "dispositivo": "Condeno ao pagamento de horas extras.",
        }],
        {},
        "",
        objetivo="criterios",
        limite=7000,
    )

    prompt = montar_prompt_manual_criterios(contexto, "ChatGPT Pro")

    assert "Provedor sugerido: ChatGPT Pro" in prompt
    assert "<document_context" in prompt
    assert "<evidence_index>" in prompt
    assert "<critical_recap" in prompt
    assert prompt.index("<document_context") < prompt.index("<task_context>")
    assert prompt.rstrip().endswith("</task_context>")
    assert '"criterios"' in prompt


def test_montar_prompt_manual_contexto_grande_usa_base_compacta_e_schema_no_fim():
    contexto = "<document_context>" + ("EVIDÊNCIA LONGA " * 2000) + "</document_context>"

    prompt = montar_prompt_manual_criterios(contexto, "ChatGPT Pro")

    assert "Base trabalhista compacta" in prompt
    assert "Jurisprudência de referência" not in prompt
    assert prompt.rfind('"criterios"') > prompt.find("<task_context>")
    assert prompt.rstrip().endswith("</task_context>")


def test_montar_prompt_manual_alertas_identifica_claude_e_schema():
    contexto = montar_contexto_longo([], {"decisao": "Nomeio perito e fixo prazo."}, "", objetivo="alertas")

    prompt = montar_prompt_manual_alertas(contexto, "Claude Pro")

    assert "Provedor sugerido: Claude Pro" in prompt
    assert "<manual_provider>Claude Pro</manual_provider>" in prompt
    assert "<task_context>" in prompt
    assert '"alertas"' in prompt


def test_extrair_json_resposta_manual_aceita_json_puro_fence_e_texto_extra():
    puro = extrair_json_resposta_manual('{"criterios": {"divisor": "220"}}')
    fenced = extrair_json_resposta_manual('```json\n{"alertas": {"prazo_laudo": "verificar"}}\n```')
    misto = extrair_json_resposta_manual('Segue o JSON:\n{"criterios": {"divisor": "180"}}\nFim.')

    assert puro["criterios"]["divisor"] == "220"
    assert fenced["alertas"]["prazo_laudo"] == "verificar"
    assert misto["criterios"]["divisor"] == "180"


def test_extrair_json_resposta_manual_retorna_erro_amigavel_sem_json():
    parsed = extrair_json_resposta_manual("não há objeto json aqui")

    assert "erro" in parsed
    assert "JSON manual inválido" in parsed["erro"]


def test_validar_resposta_manual_exige_chave_esperada():
    valido = validar_resposta_manual({"criterios": {"divisor": "220"}}, "criterios")
    invalido = validar_resposta_manual({"alertas": {}}, "criterios")

    assert valido["valido"] is True
    assert invalido["valido"] is False
    assert "criterios" in invalido["erro"]
