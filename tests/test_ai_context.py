from sintese.ai import montar_contexto_criterios


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
