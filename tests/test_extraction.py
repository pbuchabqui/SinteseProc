from sintese.extraction import extrair_dados, extrair_decisao_de_doc


def test_extrair_dados_basicos_de_texto_e_capa():
    capa = """
Processo Judicial Eletrônico
RECLAMANTE: JOAO DA SILVA
ADVOGADO: MARIA ADVOGADA
RECLAMADO: ACME LTDA
ADVOGADO: CARLOS ADVOGADO
Data da Autuação: 10/02/2024
"""
    texto = """
Processo 0001234-56.2024.5.04.0001
1ª VARA DO TRABALHO DE PORTO ALEGRE
CPF nº 123.456.789-09
CNPJ: 12.345.678/0001-90
OAB/RS 113880
Admissão: 01/03/2020
Rescisão Contratual: 15/08/2023
Cargo exercido de Analista Financeiro
"""

    dados = extrair_dados(texto, capa)

    assert dados["numero_processo"] == "0001234-56.2024.5.04.0001"
    assert dados["vara_trabalho"] == "1ª VARA DO TRABALHO DE PORTO ALEGRE"
    assert dados["reclamante"] == "JOAO DA SILVA"
    assert dados["reclamada_1"] == "ACME LTDA"
    assert dados["cpf_reclamante"] == "123.456.789-09"
    assert dados["cnpj_reclamada_1"] == "12.345.678/0001-90"
    assert dados["adv_reclamante"] == "MARIA ADVOGADA"
    assert dados["adv_reclamada_1"] == "CARLOS ADVOGADO"
    assert dados["data_admissao"] == "01/03/2020"
    assert dados["data_demissao"] == "15/08/2023"
    assert dados["data_ajuizamento"] == "10/02/2024"
    assert dados["funcao"] == "Analista Financeiro"
    assert dados["oabs"] == ["OAB/RS 113.880"]


def test_extrair_decisao_de_doc_com_dispositivo_resultado_e_verbas():
    doc_info = {
        "tipo_cat": "sentenca",
        "tipo_label": "Sentença",
        "titulo": "1. 10/04/2024 - Sentença - abc12345",
        "data": "10/04/2024",
        "texto": """
SENTENÇA
Relatório dispensado.
Ante o exposto, JULGO PARCIALMENTE PROCEDENTES os pedidos.
Condeno a reclamada ao pagamento de horas extras e FGTS.
Intimem-se.
""",
    }

    decisao = extrair_decisao_de_doc(doc_info)

    assert decisao["tipo"] == "Sentença"
    assert decisao["data"] == "10/04/2024"
    assert decisao["resultado_reclamante"] == "parcialmente procedente"
    assert "Ante o exposto" in decisao["dispositivo"]
    assert "Intimem-se" in decisao["dispositivo"]
    assert "horas extras" in decisao["verbas_deferidas"]
    assert "FGTS" in decisao["verbas_deferidas"]
