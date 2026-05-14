import sintese.extraction as extraction
from sintese.extraction import (
    aplicar_ocr_necessario,
    analisar_pdf_texto,
    anotar_decisoes_com_auditoria,
    avaliar_bloqueio_processamento,
    buscar_secoes,
    classificar_pagina_texto,
    executar_ocr_paginas,
    extrair_dados,
    extrair_decisao_de_doc,
    extrair_ficha,
    extrair_ponto,
    gerar_texto_sanitizado,
    gerar_relatorio_preprocessamento_pdf,
    montar_estrutura_pdf,
    validar_ficha,
    validar_ponto,
    _formatar_intervalos_paginas,
)


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
        "id_documento": "abc12345",
        "pagina_inicial": 12,
        "pagina_final": 14,
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
    assert decisao["id_documento"] == "abc12345"
    assert decisao["titulo_origem"] == "1. 10/04/2024 - Sentença - abc12345"
    assert decisao["pagina_inicial"] == 12
    assert decisao["pagina_final"] == 14
    assert decisao["resultado_reclamante"] == "parcialmente procedente"
    assert "Ante o exposto" in decisao["dispositivo"]
    assert "Intimem-se" in decisao["dispositivo"]
    assert "horas extras" in decisao["verbas_deferidas"]
    assert "FGTS" in decisao["verbas_deferidas"]


def test_classificar_pagina_texto_identifica_texto_nativo():
    texto = " ".join(["palavra"] * 30)

    info = classificar_pagina_texto(texto, total_imagens=0)

    assert info["status"] == "nativo"
    assert info["precisa_ocr"] is False
    assert info["palavras"] == 30


def test_classificar_pagina_texto_identifica_pagina_escaneada():
    info = classificar_pagina_texto("", total_imagens=1)

    assert info["status"] == "precisa_ocr"
    assert info["precisa_ocr"] is True
    assert info["imagens"] == 1


def test_classificar_pagina_texto_identifica_desenho_sem_texto_como_ocr():
    info = classificar_pagina_texto("", total_imagens=0, total_desenhos=3)

    assert info["status"] == "precisa_ocr"
    assert info["precisa_ocr"] is True
    assert info["desenhos"] == 3


def test_analisar_pdf_texto_resume_pdf_misto():
    class PaginaFake:
        def __init__(self, texto, imagens):
            self._texto = texto
            self._imagens = imagens

        def get_text(self):
            return self._texto

        def get_images(self, full=True):
            return [object()] * self._imagens

    paginas = [
        PaginaFake(" ".join(["texto"] * 30), 0),
        PaginaFake("", 1),
        PaginaFake("rodape", 0),
    ]

    analise = analisar_pdf_texto(paginas)

    assert analise["tipo_pdf"] == "misto"
    assert analise["total_paginas"] == 3
    assert analise["paginas_nativas"] == [1]
    assert analise["paginas_precisam_ocr"] == [2]
    assert analise["paginas_baixo_texto"] == [3]
    assert analise["percentual_nativo"] == 33.3
    assert analise["classificacao_tecnica"]["necessita_ocr"] is True
    assert analise["classificacao_tecnica"]["confianca_global"] in {"MÉDIO", "BAIXO"}


def test_analisar_pdf_texto_identifica_sumario_e_candidatos():
    class PaginaFake:
        def __init__(self, texto):
            self._texto = texto

        def get_text(self):
            return self._texto

        def get_images(self, full=True):
            return []

        def get_drawings(self):
            return []

    paginas = [
        PaginaFake("SENTENÇA\nAnte o exposto, julgo procedente o pedido."),
        PaginaFake("Cartão de ponto 01/01/2024 08:00 12:00 13:00 17:00"),
        PaginaFake("ÍNDICE\nRelação de documentos do processo"),
    ]

    analise = analisar_pdf_texto(paginas, nome_arquivo="autos.pdf", tamanho_bytes=123)
    estrutura = montar_estrutura_pdf(analise)

    assert analise["arquivo"]["nome"] == "autos.pdf"
    assert analise["sumario"]["detectado"] is True
    assert analise["auditoria"]["paginas_candidatas_decisoes"] == 1
    assert analise["auditoria"]["paginas_candidatas_ponto"] == 1
    assert any(b["nome"] == "bloco_possiveis_decisoes" for b in estrutura["blocos_sugeridos"])


def test_buscar_secoes_usa_textos_paginas_ocr_no_toc():
    class PaginaFake:
        def get_text(self):
            return ""

    class DocFake:
        def __init__(self):
            self.paginas = [PaginaFake()]

        def __len__(self):
            return len(self.paginas)

        def __getitem__(self, index):
            return self.paginas[index]

    toc = [[1, "1. 10/04/2024 - Sentença - abc12345", 1]]
    textos_paginas = [
        "SENTENÇA\nAnte o exposto, JULGO PARCIALMENTE PROCEDENTES os pedidos. Intimem-se."
    ]

    secs = buscar_secoes(DocFake(), toc, "", textos_paginas=textos_paginas)

    assert secs["documentos_decisao"]
    assert "JULGO PARCIALMENTE" in secs["documentos_decisao"][0]["texto"]
    assert secs["documentos_decisao"][0]["id_documento"] == "abc12345"
    assert secs["documentos_decisao"][0]["pagina_inicial"] == 1
    assert secs["documentos_decisao"][0]["pagina_final"] == 1


def test_relatorio_preprocessamento_informa_ocr_e_bloqueio():
    estrutura = {
        "arquivo": {"nome": "autos.pdf", "paginas_totais": 1, "tamanho_bytes": 10, "criptografado": False},
        "classificacao_tecnica": {"tipo_pdf": "escaneado", "confianca_global": "BAIXO", "necessita_ocr": True},
        "sumario": {"detectado": False, "pagina_inicial_pdf": None, "pagina_final_pdf": None},
        "paginas": [{"pagina_pdf": 1, "alertas": ["página sem texto extraível"]}],
        "blocos_sugeridos": [],
        "auditoria": {"paginas_totais": 1, "paginas_ocr": 1, "alertas_emitidos": 1},
        "alertas": ["conferência humana necessária"],
    }

    relatorio = gerar_relatorio_preprocessamento_pdf(estrutura)

    assert "Relatório de Pré-Processamento" in relatorio
    assert "Confiança global: BAIXO" in relatorio
    assert "conferência humana necessária" in relatorio


def test_avaliar_bloqueio_processamento_critico_bloqueia():
    estrutura = {"classificacao_tecnica": {"confianca_global": "CRÍTICO"}}

    resultado = avaliar_bloqueio_processamento(estrutura)

    assert resultado["bloquear"] is True
    assert "CRÍTICA" in resultado["mensagem"]


def test_avaliar_bloqueio_processamento_baixo_apenas_alerta():
    estrutura = {"classificacao_tecnica": {"confianca_global": "BAIXO"}}

    resultado = avaliar_bloqueio_processamento(estrutura)

    assert resultado["bloquear"] is False
    assert "BAIXA" in resultado["mensagem"]


def test_gerar_texto_sanitizado_preserva_paginas():
    texto = gerar_texto_sanitizado(["capa", "sentença"])

    assert "[PÁGINA 1]\ncapa" in texto
    assert "[PÁGINA 2]\nsentença" in texto


def test_montar_estrutura_pdf_inclui_backends_por_pagina():
    class PaginaFake:
        def get_text(self):
            return " ".join(["texto"] * 30)

        def get_images(self, full=True):
            return []

        def get_drawings(self):
            return []

    analise = analisar_pdf_texto([PaginaFake()])
    estrutura = montar_estrutura_pdf(analise)

    assert estrutura["paginas"][0]["backend_texto"] == "pymupdf"
    assert "backend_tabela" in estrutura["paginas"][0]


def test_anotar_decisoes_com_auditoria_marca_ocr_e_baixa_confianca():
    decisoes = [{"tipo": "Sentença", "pagina_inicial": 2, "pagina_final": 2}]
    estrutura = {
        "paginas": [
            {
                "pagina_pdf": 2,
                "ocr_aplicado": True,
                "confianca": "BAIXO",
                "alertas": ["texto obtido por OCR"],
            }
        ]
    }

    anotadas = anotar_decisoes_com_auditoria(decisoes, estrutura)

    auditoria = anotadas[0]["auditoria_extracao"]
    assert auditoria["ocr_aplicado"] is True
    assert auditoria["confianca_minima"] == "BAIXO"
    assert "decisão extraída de página OCRizada" in auditoria["alertas"]


def test_extrair_ficha_prefere_pdfplumber_quando_disponivel(monkeypatch):
    monkeypatch.setattr(
        extraction,
        "extrair_tabelas_pdfplumber",
        lambda pdf_bytes, textos_paginas=None, padrao_paginas=None: [
            {
                "pagina": 1,
                "backend": "pdfplumber",
                "linhas": [
                    ["Competência", "Salário Base", "INSS"],
                    ["01/2024", "1.500,00", "120,00"],
                ],
            }
        ],
    )

    ficha = extrair_ficha([], textos_paginas=["FICHA FINANCEIRA"], pdf_bytes=b"%PDF")

    assert ficha["backend"] == "pdfplumber"
    assert ficha["rubricas"] == ["INSS", "Salário Base"]
    assert ficha["competencias"][0]["competencia"] == "01/2024"
    assert ficha["competencias"][0]["valores"]["Salário Base"]["valor"] == 1500.0
    assert ficha["validacao"]["ok"] is True


def test_validar_ficha_alerta_sem_competencias():
    validacao = validar_ficha({"rubricas": ["Salário"], "competencias": []})

    assert validacao["ok"] is False
    assert "Ficha sem competências válidas extraídas." in validacao["alertas"]


def test_extrair_ponto_prefere_pdfplumber_quando_disponivel(monkeypatch):
    monkeypatch.setattr(
        extraction,
        "extrair_tabelas_pdfplumber",
        lambda pdf_bytes, textos_paginas=None, padrao_paginas=None: [
            {
                "pagina": 1,
                "backend": "pdfplumber",
                "linhas": [
                    ["Data", "Dia", "Entrada", "Saída", "Entrada", "Saída"],
                    ["01/02/2024", "QUI", "08:00", "12:00", "13:00", "17:00"],
                ],
            }
        ],
    )

    ponto = extrair_ponto([], textos_paginas=["ESPELHO DE PONTO"], pdf_bytes=b"%PDF")

    assert ponto["backend"] == "pdfplumber"
    assert ponto["registros"][0]["data"] == "01/02/2024"
    assert ponto["registros"][0]["dia_semana"] == "QUI"
    assert ponto["registros"][0]["entradas_saidas"] == ["08:00", "12:00", "13:00", "17:00"]
    assert ponto["registros"][0]["horas_trabalhadas"] == ""
    assert ponto["registros"][0]["horas_extras"] == ""
    assert ponto["validacao"]["total_marcacoes"] == 4


def test_extrair_ponto_separa_totalizadores_quando_cabecalho_indica(monkeypatch):
    monkeypatch.setattr(
        extraction,
        "extrair_tabelas_pdfplumber",
        lambda pdf_bytes, textos_paginas=None, padrao_paginas=None: [
            {
                "pagina": 1,
                "backend": "pdfplumber",
                "linhas": [
                    ["Data", "Dia", "E1", "S1", "H.Trab.", "H.Extra"],
                    ["01/02/2024", "QUI", "08:00", "12:00", "04:00", "01:00"],
                ],
            }
        ],
    )

    ponto = extrair_ponto([], textos_paginas=["ESPELHO DE PONTO"], pdf_bytes=b"%PDF")

    assert ponto["registros"][0]["entradas_saidas"] == ["08:00", "12:00"]
    assert ponto["registros"][0]["horas_trabalhadas"] == "04:00"
    assert ponto["registros"][0]["horas_extras"] == "01:00"


def test_validar_ponto_alerta_marcacao_impar():
    ponto = {
        "registros": [
            {"data": "01/02/2024", "entradas_saidas": ["08:00", "12:00", "13:00"]}
        ]
    }

    validacao = validar_ponto(ponto)

    assert validacao["ok"] is False
    assert any("Marcações ímpares" in alerta for alerta in validacao["alertas"])


def test_aplicar_ocr_necessario_reconstroi_texto_com_paginas_processadas(monkeypatch):
    class PaginaFake:
        def __init__(self, texto):
            self._texto = texto

        def get_text(self):
            return self._texto

    class DocFake:
        def __init__(self):
            self.paginas = [PaginaFake("texto nativo pagina 1"), PaginaFake("")]

        def __iter__(self):
            return iter(self.paginas)

        def __getitem__(self, index):
            return self.paginas[index]

    def fake_executar_ocr_paginas(doc, paginas, idioma="por+eng", dpi=300, pdf_bytes=None):
        return {
            "paginas_solicitadas": paginas,
            "paginas_processadas": [2],
            "textos_ocr": {2: "texto reconhecido por OCR"},
            "erros": {},
            "idioma": idioma,
            "dpi": dpi,
            "engine": "teste",
        }

    monkeypatch.setattr(extraction, "executar_ocr_paginas", fake_executar_ocr_paginas)

    resultado = aplicar_ocr_necessario(
        DocFake(),
        {"paginas_precisam_ocr": [2]},
    )

    assert resultado["executado"] is True
    assert resultado["paginas_processadas"] == [2]
    assert resultado["capa"] == "texto nativo pagina 1"
    assert "[PÁGINA 2]\ntexto reconhecido por OCR" in resultado["texto_completo"]


def test_aplicar_ocr_necessario_atualiza_capa_quando_primeira_pagina_tem_ocr(monkeypatch):
    class PaginaFake:
        def get_text(self):
            return ""

    class DocFake:
        def __iter__(self):
            return iter([PaginaFake()])

        def __getitem__(self, index):
            return PaginaFake()

    def fake_executar_ocr_paginas(doc, paginas, idioma="por+eng", dpi=300, pdf_bytes=None):
        return {
            "paginas_solicitadas": paginas,
            "paginas_processadas": [1],
            "textos_ocr": {1: "CAPA OCR RECLAMANTE: JOAO"},
            "erros": {},
            "idioma": idioma,
            "dpi": dpi,
            "engine": "teste",
        }

    monkeypatch.setattr(extraction, "executar_ocr_paginas", fake_executar_ocr_paginas)

    resultado = aplicar_ocr_necessario(
        DocFake(),
        {"paginas_precisam_ocr": [1]},
    )

    assert resultado["capa"] == "CAPA OCR RECLAMANTE: JOAO"
    assert resultado["texto_completo"] == "[PÁGINA 1]\nCAPA OCR RECLAMANTE: JOAO"


def test_formatar_intervalos_paginas_compacta_ranges():
    assert _formatar_intervalos_paginas([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"


def test_executar_ocr_paginas_faz_fallback_quando_ocrmypdf_falha(monkeypatch):
    class PaginaFake:
        def get_textpage_ocr(self, language="por+eng", dpi=300, full=True):
            return object()

        def get_text(self, kind="text", textpage=None):
            return "texto fallback"

    class DocFake:
        def __getitem__(self, index):
            return PaginaFake()

    monkeypatch.setattr(extraction.shutil, "which", lambda cmd: "/usr/bin/ocrmypdf")
    monkeypatch.setattr(
        extraction,
        "executar_ocr_paginas_ocrmypdf",
        lambda *args, **kwargs: {
            "paginas_solicitadas": [1],
            "paginas_processadas": [],
            "textos_ocr": {},
            "erros": {1: "falhou"},
            "engine": "ocrmypdf",
        },
    )

    resultado = executar_ocr_paginas(DocFake(), [1, 2, 3], pdf_bytes=b"%PDF")

    assert resultado["engine"] == "pymupdf_tesseract"
    assert resultado["textos_ocr"] == {
        1: "texto fallback",
        2: "texto fallback",
        3: "texto fallback",
    }


def test_executar_ocr_paginas_prefere_ocrmypdf_quando_disponivel(monkeypatch):
    monkeypatch.setattr(extraction.shutil, "which", lambda cmd: "/usr/bin/ocrmypdf")
    monkeypatch.setattr(
        extraction,
        "executar_ocr_paginas_ocrmypdf",
        lambda *args, **kwargs: {
            "paginas_solicitadas": [1],
            "paginas_processadas": [1],
            "textos_ocr": {1: "texto acurado"},
            "erros": {},
            "engine": "ocrmypdf",
        },
    )

    resultado = executar_ocr_paginas(object(), [1], pdf_bytes=b"%PDF")

    assert resultado["engine"] == "ocrmypdf"
    assert resultado["textos_ocr"] == {1: "texto acurado"}
