from sintese.ai import montar_contexto_criterios, montar_contexto_longo, selecionar_blocos_contexto


def test_montar_contexto_criterios_inclui_sentenca_e_acordao_extraidos():
    decisoes = [
        {
            "tipo": "Sentença",
            "data": "10/04/2024",
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

    assert "## Mapa de Evidências" in contexto
    assert "EV-01" in contexto
    assert "Sentença deferiu horas extras" in contexto
    assert "Acórdão manteve a condenação" in contexto
    assert "## Reforço Final" in contexto


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
    assert "Páginas | Confiança" in contexto
    assert "| 2 | MÉDIO |" in contexto
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
    assert "Reforço Final" in contexto or "evidências críticas preservadas" in contexto


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
