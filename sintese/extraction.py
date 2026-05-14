"""Deterministic PDF and text extraction helpers for SinteseProc."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz

MIN_CHARS_TEXTO_NATIVO = 80
MIN_PALAVRAS_TEXTO_NATIVO = 15
IDIOMA_OCR_PADRAO = "por+eng"
OCR_DPI_PADRAO = 300
OCR_JOBS_MAX = 4
OCRMY_PDF_MIN_PAGINAS = 1
CONFIANCA_BLOQUEIO = {"BAIXO", "CRÍTICO"}


def ler_pdf(b: bytes) -> tuple[fitz.Document, str, str, list, int]:
    """Abre o PDF e retorna (doc, capa, texto_completo, toc, total_paginas).
    capa = texto da página 1 (cabeçalho PJe com partes explícitas).
    toc  = sumário estruturado do PDF (lista de [nivel, titulo, pagina]).
    """
    doc = fitz.open(stream=b, filetype="pdf")
    textos_paginas = extrair_textos_paginas(doc)
    capa = textos_paginas[0] if textos_paginas else ""
    toc  = doc.get_toc()  # sumário embutido — disponível em todos os PDFs PJe
    return doc, capa, juntar_textos_paginas(textos_paginas), toc, len(textos_paginas)


def extrair_textos_paginas(doc: fitz.Document) -> list[str]:
    return [p.get_text() for p in doc]


def juntar_textos_paginas(textos_paginas: list[str]) -> str:
    paginas = [f"[PÁGINA {i+1}]\n{texto}" for i, texto in enumerate(textos_paginas)]
    return "\n".join(paginas)


def _confianca_por_status(status: str, total_chars: int, total_palavras: int, tem_visual: bool) -> str:
    if status == "nativo":
        return "ALTO"
    if status == "precisa_ocr":
        return "BAIXO"
    if status == "sem_texto":
        return "CRÍTICO" if tem_visual else "BAIXO"
    if total_chars >= 40 or total_palavras >= 8:
        return "MÉDIO"
    return "BAIXO"


def _possiveis_tipos_pagina(texto: str) -> list[str]:
    tipos = []
    padroes = [
        ("decisao", r"SENTEN[ÇC]A|AC[ÓO]RD[ÃA]O|ANTE\s+O\s+EXPOSTO|JULGO|DECIDO|EMBARGOS\s+DE\s+DECLARA"),
        ("ponto", r"CART[AÃ]O\s+DE\s+PONTO|ESPELHO\s+DE\s+PONTO|REGISTRO\s+DE\s+PONTO|\b\d{2}:\d{2}\b"),
        ("holerite", r"HOLERITE|CONTRACHEQUE|FICHA\s+FINANCEIRA|FOLHA\s+DE\s+PAGAMENTO|PROVENTOS|DESCONTOS"),
        ("calculos", r"MEM[ÓO]RIA\s+DE\s+C[ÁA]LCULO|DEMONSTRATIVO\s+DE\s+C[ÁA]LCULO|ATUALIZA[ÇC][ÃA]O|JUROS|SELIC|IPCA"),
        ("sumario", r"\b[ÍI]NDICE\b|SUM[ÁA]RIO|RELA[ÇC][ÃA]O\s+DE\s+DOCUMENTOS|DOCUMENTOS\s+DO\s+PROCESSO"),
    ]
    for nome, padrao in padroes:
        if re.search(padrao, texto or "", re.IGNORECASE):
            tipos.append(nome)
    return tipos


def _alertas_pagina(texto: str, status: str, total_imagens: int, total_desenhos: int) -> list[str]:
    alertas = []
    if status == "precisa_ocr":
        alertas.append("página com conteúdo visual e texto nativo insuficiente")
    if status == "sem_texto":
        alertas.append("página sem texto extraível")
    if status == "baixo_texto":
        alertas.append("texto nativo baixo ou fragmentado")
    if re.search(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{0,3}\b", texto or ""):
        alertas.append("possível número de processo incompleto")
    if total_imagens and total_desenhos:
        alertas.append("página híbrida com imagens e elementos vetoriais")
    return alertas


def classificar_pagina_texto(
    texto: str,
    total_imagens: int,
    total_desenhos: int = 0,
    min_chars: int = MIN_CHARS_TEXTO_NATIVO,
    min_palavras: int = MIN_PALAVRAS_TEXTO_NATIVO,
) -> dict:
    """Classifica a camada textual de uma página para decidir se OCR é necessário."""
    texto_limpo = re.sub(r"\s+", " ", texto or "").strip()
    palavras = re.findall(r"\w+", texto_limpo, re.UNICODE)
    total_chars = len(texto_limpo)
    total_palavras = len(palavras)

    tem_conteudo_visual = total_imagens > 0 or total_desenhos > 0

    if total_chars >= min_chars and total_palavras >= min_palavras:
        status = "nativo"
        precisa_ocr = False
    elif tem_conteudo_visual and total_palavras < min_palavras:
        status = "precisa_ocr"
        precisa_ocr = True
    elif total_chars == 0:
        status = "sem_texto"
        precisa_ocr = False
    else:
        status = "baixo_texto"
        precisa_ocr = False

    confianca = _confianca_por_status(status, total_chars, total_palavras, tem_conteudo_visual)

    return {
        "status": status,
        "precisa_ocr": precisa_ocr,
        "confianca": confianca,
        "texto_extraivel": total_chars > 0,
        "chars": total_chars,
        "palavras": total_palavras,
        "imagens": total_imagens,
        "desenhos": total_desenhos,
        "possiveis_tipos": _possiveis_tipos_pagina(texto_limpo),
        "alertas": _alertas_pagina(texto_limpo, status, total_imagens, total_desenhos),
    }


def _confianca_global(detalhes: list[dict], paginas_precisam_ocr: list[int]) -> str:
    if not detalhes:
        return "CRÍTICO"
    if any(p["confianca"] == "CRÍTICO" for p in detalhes):
        return "CRÍTICO"
    total = len(detalhes)
    baixo = sum(1 for p in detalhes if p["confianca"] == "BAIXO")
    medio = sum(1 for p in detalhes if p["confianca"] == "MÉDIO")
    if baixo / total >= 0.35:
        return "BAIXO"
    if paginas_precisam_ocr or medio:
        return "MÉDIO"
    return "ALTO"


def _buscar_sumario_final(detalhes: list[dict], janela: int = 40) -> dict:
    candidatos = [p for p in detalhes[-janela:] if "sumario" in p.get("possiveis_tipos", [])]
    if not candidatos:
        candidatos = [p for p in detalhes if "sumario" in p.get("possiveis_tipos", [])]
    if not candidatos:
        return {
            "detectado": False,
            "pagina_inicial_pdf": None,
            "pagina_final_pdf": None,
            "confianca": "",
            "observacoes": ["Sumário/índice PJe não localizado por texto."],
        }
    return {
        "detectado": True,
        "pagina_inicial_pdf": candidatos[0]["pagina"],
        "pagina_final_pdf": candidatos[-1]["pagina"],
        "confianca": "ALTO" if all(p["confianca"] in {"ALTO", "MÉDIO"} for p in candidatos) else "MÉDIO",
        "observacoes": [],
    }


def _contar_tipo(detalhes: list[dict], tipo: str) -> int:
    return sum(1 for p in detalhes if tipo in p.get("possiveis_tipos", []))


def analisar_pdf_texto(doc: fitz.Document, nome_arquivo: str = "", tamanho_bytes: int = 0) -> dict:
    """Analisa se o PDF tem texto nativo aproveitável ou páginas que pedem OCR."""
    detalhes = []
    textos_paginas = []
    for idx, pagina in enumerate(doc, 1):
        texto = pagina.get_text()
        textos_paginas.append(texto)
        try:
            total_imagens = len(pagina.get_images(full=True))
        except Exception:
            total_imagens = 0
        try:
            total_desenhos = len(pagina.get_drawings())
        except Exception:
            total_desenhos = 0
        info = classificar_pagina_texto(texto, total_imagens, total_desenhos)
        info["pagina"] = idx
        info["pagina_pdf"] = idx
        info["ocr_aplicado"] = False
        detalhes.append(info)

    total = len(detalhes)
    paginas_nativas = [p["pagina"] for p in detalhes if p["status"] == "nativo"]
    paginas_precisam_ocr = [p["pagina"] for p in detalhes if p["precisa_ocr"]]
    paginas_baixo_texto = [p["pagina"] for p in detalhes if p["status"] == "baixo_texto"]
    paginas_sem_texto = [p["pagina"] for p in detalhes if p["status"] == "sem_texto"]

    if total == 0:
        tipo_pdf = "vazio"
    elif len(paginas_precisam_ocr) == total:
        tipo_pdf = "escaneado"
    elif paginas_precisam_ocr:
        tipo_pdf = "misto"
    elif len(paginas_nativas) == total:
        tipo_pdf = "nativo"
    else:
        tipo_pdf = "baixo_texto"

    possui_tabelas = any(
        tipo in p.get("possiveis_tipos", [])
        for p in detalhes
        for tipo in ("ponto", "holerite", "calculos")
    )
    sumario = _buscar_sumario_final(detalhes)
    confianca_global = _confianca_global(detalhes, paginas_precisam_ocr)
    try:
        criptografado = bool(getattr(doc, "needs_pass", False))
    except Exception:
        criptografado = False
    try:
        metadados = dict(getattr(doc, "metadata", {}) or {})
    except Exception:
        metadados = {}

    alertas = []
    if criptografado:
        alertas.append("PDF criptografado ou com restrição de acesso.")
    if confianca_global in CONFIANCA_BLOQUEIO:
        alertas.append(
            "A confiabilidade técnica do PDF é insuficiente para transcrição ou extração automática segura. "
            "É necessária conferência humana das páginas indicadas antes do uso pericial."
        )

    return {
        "tipo_pdf": tipo_pdf,
        "total_paginas": total,
        "total_nativas": len(paginas_nativas),
        "total_precisam_ocr": len(paginas_precisam_ocr),
        "total_baixo_texto": len(paginas_baixo_texto),
        "total_sem_texto": len(paginas_sem_texto),
        "percentual_nativo": round((len(paginas_nativas) / total) * 100, 1) if total else 0.0,
        "paginas_nativas": paginas_nativas,
        "paginas_precisam_ocr": paginas_precisam_ocr,
        "paginas_baixo_texto": paginas_baixo_texto,
        "paginas_sem_texto": paginas_sem_texto,
        "detalhes_paginas": detalhes,
        "textos_paginas": textos_paginas,
        "classificacao_tecnica": {
            "tipo_pdf": tipo_pdf,
            "confianca_global": confianca_global,
            "necessita_ocr": bool(paginas_precisam_ocr),
            "possui_tabelas": possui_tabelas,
            "possui_sumario": sumario["detectado"],
        },
        "sumario": sumario,
        "auditoria": {
            "paginas_totais": total,
            "paginas_texto_nativo": len(paginas_nativas),
            "paginas_ocr": 0,
            "paginas_ocr_baixo": 0,
            "paginas_ilegiveis": len([p for p in detalhes if p["confianca"] == "CRÍTICO"]),
            "paginas_em_branco": len(paginas_sem_texto),
            "tabelas_detectadas": 0,
            "tabelas_extraidas": 0,
            "paginas_candidatas_decisoes": _contar_tipo(detalhes, "decisao"),
            "paginas_candidatas_ponto": _contar_tipo(detalhes, "ponto"),
            "paginas_candidatas_holerites": _contar_tipo(detalhes, "holerite"),
            "paginas_candidatas_calculos": _contar_tipo(detalhes, "calculos"),
            "alertas_emitidos": len(alertas) + sum(len(p.get("alertas", [])) for p in detalhes),
        },
        "arquivo": {
            "nome": nome_arquivo,
            "paginas_totais": total,
            "tamanho_bytes": tamanho_bytes,
            "criptografado": criptografado,
            "metadados": metadados,
        },
        "alertas": alertas,
    }


def executar_ocr_paginas(
    doc: fitz.Document,
    paginas: list[int],
    idioma: str = IDIOMA_OCR_PADRAO,
    dpi: int = OCR_DPI_PADRAO,
    pdf_bytes: bytes | None = None,
    preferir_ocrmypdf: bool = True,
) -> dict:
    """Executa OCR nas páginas informadas e retorna texto extraído por página."""
    paginas_unicas = sorted(set(paginas))
    if (
        preferir_ocrmypdf
        and pdf_bytes
        and len(paginas_unicas) >= OCRMY_PDF_MIN_PAGINAS
        and shutil.which("ocrmypdf")
    ):
        resultado = executar_ocr_paginas_ocrmypdf(
            pdf_bytes,
            paginas_unicas,
            idioma=idioma,
            dpi=dpi,
        )
        if resultado["paginas_processadas"]:
            return resultado

    return executar_ocr_paginas_pymupdf(doc, paginas, idioma=idioma, dpi=dpi)


def executar_ocr_paginas_pymupdf(
    doc: fitz.Document,
    paginas: list[int],
    idioma: str = IDIOMA_OCR_PADRAO,
    dpi: int = OCR_DPI_PADRAO,
) -> dict:
    """Executa OCR página a página via PyMuPDF/Tesseract."""
    textos_ocr = {}
    erros = {}

    for pagina_num in paginas:
        try:
            pagina = doc[pagina_num - 1]
            textpage = pagina.get_textpage_ocr(language=idioma, dpi=dpi, full=True)
            texto = pagina.get_text("text", textpage=textpage)
            textos_ocr[pagina_num] = texto or ""
        except Exception as exc:
            erros[pagina_num] = str(exc)

    return {
        "paginas_solicitadas": paginas,
        "paginas_processadas": sorted(textos_ocr),
        "textos_ocr": textos_ocr,
        "erros": erros,
        "idioma": idioma,
        "dpi": dpi,
        "engine": "pymupdf_tesseract",
    }


def executar_ocr_paginas_ocrmypdf(
    pdf_bytes: bytes,
    paginas: list[int],
    idioma: str = IDIOMA_OCR_PADRAO,
    dpi: int = OCR_DPI_PADRAO,
    jobs: int | None = None,
) -> dict:
    """Executa OCR com OCRmyPDF, preferindo acurácia e paralelismo."""
    paginas = sorted(set(paginas))
    if not paginas:
        return {
            "paginas_solicitadas": [],
            "paginas_processadas": [],
            "textos_ocr": {},
            "erros": {},
            "idioma": idioma,
            "dpi": dpi,
            "engine": "ocrmypdf",
        }

    jobs = jobs or min(max(os.cpu_count() or 1, 1), OCR_JOBS_MAX)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        entrada = tmp / "entrada.pdf"
        saida = tmp / "saida.pdf"
        entrada.write_bytes(pdf_bytes)

        cmd = [
            "ocrmypdf",
            "--quiet",
            "--force-ocr",
            "--deskew",
            "--rotate-pages",
            "--rotate-pages-threshold",
            "2",
            "--oversample",
            str(dpi),
            "--jobs",
            str(jobs),
            "--pages",
            _formatar_intervalos_paginas(paginas),
            "--output-type",
            "pdf",
            "-l",
            idioma,
            str(entrada),
            str(saida),
        ]

        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(60, len(paginas) * 45),
        )
        if proc.returncode != 0 or not saida.exists():
            erro = (proc.stderr or proc.stdout or "OCRmyPDF falhou sem mensagem.").strip()
            return {
                "paginas_solicitadas": paginas,
                "paginas_processadas": [],
                "textos_ocr": {},
                "erros": {p: erro for p in paginas},
                "idioma": idioma,
                "dpi": dpi,
                "engine": "ocrmypdf",
            }

        textos_ocr = {}
        erros = {}
        with fitz.open(str(saida)) as doc_ocr:
            for pagina_num in paginas:
                try:
                    textos_ocr[pagina_num] = doc_ocr[pagina_num - 1].get_text() or ""
                except Exception as exc:
                    erros[pagina_num] = str(exc)

        return {
            "paginas_solicitadas": paginas,
            "paginas_processadas": sorted(textos_ocr),
            "textos_ocr": textos_ocr,
            "erros": erros,
            "idioma": idioma,
            "dpi": dpi,
            "engine": "ocrmypdf",
            "jobs": jobs,
        }


def _formatar_intervalos_paginas(paginas: list[int]) -> str:
    paginas = sorted(set(paginas))
    intervalos = []
    inicio = fim = paginas[0]

    for pagina in paginas[1:]:
        if pagina == fim + 1:
            fim = pagina
            continue
        intervalos.append(f"{inicio}-{fim}" if inicio != fim else str(inicio))
        inicio = fim = pagina

    intervalos.append(f"{inicio}-{fim}" if inicio != fim else str(inicio))
    return ",".join(intervalos)


def aplicar_ocr_necessario(
    doc: fitz.Document,
    analise_pdf: dict,
    pdf_bytes: bytes | None = None,
    idioma: str = IDIOMA_OCR_PADRAO,
    dpi: int = OCR_DPI_PADRAO,
) -> dict:
    """Executa OCR nas páginas sinalizadas e reconstrói capa/texto completo."""
    paginas = list(analise_pdf.get("paginas_precisam_ocr") or [])
    textos_paginas = list(analise_pdf.get("textos_paginas") or extrair_textos_paginas(doc))
    detalhes_paginas = [dict(p) for p in analise_pdf.get("detalhes_paginas", [])]

    if not paginas:
        return {
            "executado": False,
            "paginas_processadas": [],
            "erros": {},
            "textos_paginas": textos_paginas,
            "capa": textos_paginas[0] if textos_paginas else "",
            "texto_completo": juntar_textos_paginas(textos_paginas),
            "detalhes_paginas": detalhes_paginas,
            "estrutura_pdf": montar_estrutura_pdf(analise_pdf, textos_paginas=textos_paginas),
        }

    resultado = executar_ocr_paginas(
        doc,
        paginas,
        idioma=idioma,
        dpi=dpi,
        pdf_bytes=pdf_bytes,
    )
    for pagina_num, texto in resultado["textos_ocr"].items():
        if texto.strip() and 1 <= pagina_num <= len(textos_paginas):
            textos_paginas[pagina_num - 1] = texto
            if pagina_num <= len(detalhes_paginas):
                detalhes_paginas[pagina_num - 1]["ocr_aplicado"] = True
                detalhes_paginas[pagina_num - 1]["confianca"] = "MÉDIO"
                detalhes_paginas[pagina_num - 1]["texto_extraivel"] = True
                detalhes_paginas[pagina_num - 1]["chars"] = len(re.sub(r"\s+", " ", texto).strip())
                detalhes_paginas[pagina_num - 1]["palavras"] = len(re.findall(r"\w+", texto, re.UNICODE))
                detalhes_paginas[pagina_num - 1]["possiveis_tipos"] = _possiveis_tipos_pagina(texto)
                detalhes_paginas[pagina_num - 1]["alertas"] = ["texto obtido por OCR; conferir trechos decisórios literais"]

    analise_atualizada = dict(analise_pdf)
    analise_atualizada["detalhes_paginas"] = detalhes_paginas
    analise_atualizada["textos_paginas"] = textos_paginas
    auditoria = dict(analise_atualizada.get("auditoria", {}))
    auditoria["paginas_ocr"] = len(resultado["paginas_processadas"])
    auditoria["paginas_ocr_baixo"] = len(resultado["erros"])
    auditoria["alertas_emitidos"] = len(analise_atualizada.get("alertas", [])) + sum(
        len(p.get("alertas", [])) for p in detalhes_paginas
    )
    analise_atualizada["auditoria"] = auditoria
    analise_atualizada["classificacao_tecnica"] = dict(analise_atualizada.get("classificacao_tecnica", {}))
    analise_atualizada["classificacao_tecnica"]["necessita_ocr"] = bool(resultado["erros"])
    confianca_atualizada = _confianca_global(
        detalhes_paginas,
        sorted(resultado["erros"]),
    )
    analise_atualizada["classificacao_tecnica"]["confianca_global"] = confianca_atualizada
    alertas_atualizados = [
        a for a in analise_atualizada.get("alertas", [])
        if "confiabilidade técnica do PDF é insuficiente" not in a
    ]
    if confianca_atualizada in CONFIANCA_BLOQUEIO:
        alertas_atualizados.append(
            "A confiabilidade técnica do PDF é insuficiente para transcrição ou extração automática segura. "
            "É necessária conferência humana das páginas indicadas antes do uso pericial."
        )
    analise_atualizada["alertas"] = alertas_atualizados
    auditoria["alertas_emitidos"] = len(alertas_atualizados) + sum(
        len(p.get("alertas", [])) for p in detalhes_paginas
    )
    analise_atualizada["auditoria"] = auditoria

    return {
        "executado": True,
        "paginas_processadas": resultado["paginas_processadas"],
        "erros": resultado["erros"],
        "textos_paginas": textos_paginas,
        "capa": textos_paginas[0] if textos_paginas else "",
        "texto_completo": juntar_textos_paginas(textos_paginas),
        "idioma": idioma,
        "dpi": dpi,
        "engine": resultado.get("engine"),
        "jobs": resultado.get("jobs"),
        "detalhes_paginas": detalhes_paginas,
        "estrutura_pdf": montar_estrutura_pdf(analise_atualizada, textos_paginas=textos_paginas),
    }


def montar_estrutura_pdf(analise_pdf: dict, textos_paginas: list[str] | None = None) -> dict:
    """Monta um resumo auditável do pré-processamento em formato serializável."""
    detalhes = analise_pdf.get("detalhes_paginas", [])
    textos_paginas = textos_paginas or analise_pdf.get("textos_paginas") or []
    blocos = []
    for tipo, nome in [
        ("decisao", "bloco_possiveis_decisoes"),
        ("ponto", "bloco_cartoes_ponto"),
        ("holerite", "bloco_holerites"),
        ("calculos", "bloco_calculos"),
    ]:
        paginas = [p["pagina"] for p in detalhes if tipo in p.get("possiveis_tipos", [])]
        if paginas:
            blocos.append({
                "nome": nome,
                "pagina_inicial_pdf": min(paginas),
                "pagina_final_pdf": max(paginas),
                "motivo": f"páginas com indícios textuais de {tipo}",
                "confianca": "MÉDIO",
            })

    paginas = []
    for idx, info in enumerate(detalhes):
        texto = textos_paginas[idx] if idx < len(textos_paginas) else ""
        paginas.append({
            "pagina_pdf": info.get("pagina", idx + 1),
            "texto_extraivel": bool(texto.strip() or info.get("texto_extraivel")),
            "ocr_aplicado": bool(info.get("ocr_aplicado")),
            "confianca": info.get("confianca", ""),
            "possiveis_tipos": info.get("possiveis_tipos", []),
            "alertas": info.get("alertas", []),
        })

    return {
        "arquivo": analise_pdf.get("arquivo", {}),
        "classificacao_tecnica": analise_pdf.get("classificacao_tecnica", {}),
        "sumario": analise_pdf.get("sumario", {}),
        "paginas": paginas,
        "blocos_sugeridos": blocos,
        "tabelas": [],
        "auditoria": analise_pdf.get("auditoria", {}),
        "alertas": analise_pdf.get("alertas", []),
    }


def gerar_relatorio_preprocessamento_pdf(estrutura_pdf: dict) -> str:
    """Gera relatório técnico curto sobre qualidade, OCR e blocos candidatos."""
    arquivo = estrutura_pdf.get("arquivo", {})
    cls = estrutura_pdf.get("classificacao_tecnica", {})
    aud = estrutura_pdf.get("auditoria", {})
    sumario = estrutura_pdf.get("sumario", {})
    linhas = [
        "# Relatório de Pré-Processamento do PDF",
        "",
        "## 1. Dados do Arquivo",
        f"- Nome: {arquivo.get('nome') or ''}",
        f"- Páginas: {arquivo.get('paginas_totais') or aud.get('paginas_totais') or 0}",
        f"- Tamanho: {arquivo.get('tamanho_bytes') or 0} bytes",
        f"- Criptografado: {'sim' if arquivo.get('criptografado') else 'não'}",
        "",
        "## 2. Classificação Técnica",
        f"- Tipo: {cls.get('tipo_pdf') or ''}",
        f"- Confiança global: {cls.get('confianca_global') or ''}",
        f"- Necessita OCR: {'sim' if cls.get('necessita_ocr') else 'não'}",
        f"- Possui indícios de tabelas: {'sim' if cls.get('possui_tabelas') else 'não'}",
        "",
        "## 3. Pesquisabilidade e OCR",
        f"- Páginas com texto nativo: {aud.get('paginas_texto_nativo', 0)}",
        f"- Páginas OCRizadas: {aud.get('paginas_ocr', 0)}",
        f"- Páginas ilegíveis/críticas: {aud.get('paginas_ilegiveis', 0)}",
        "",
        "## 4. Sumário / Índice PJe",
        f"- Detectado: {'sim' if sumario.get('detectado') else 'não'}",
        f"- Páginas: {sumario.get('pagina_inicial_pdf')} a {sumario.get('pagina_final_pdf')}",
        "",
        "## 5. Blocos Documentais Sugeridos",
    ]
    blocos = estrutura_pdf.get("blocos_sugeridos") or []
    if blocos:
        for bloco in blocos:
            linhas.append(
                f"- {bloco.get('nome')}: páginas {bloco.get('pagina_inicial_pdf')} a "
                f"{bloco.get('pagina_final_pdf')} ({bloco.get('confianca')})"
            )
    else:
        linhas.append("- Nenhum bloco sugerido por heurística textual.")

    linhas += [
        "",
        "## 6. Alertas",
    ]
    alertas = list(estrutura_pdf.get("alertas") or [])
    for pagina in estrutura_pdf.get("paginas") or []:
        for alerta in pagina.get("alertas", []):
            alertas.append(f"Página {pagina.get('pagina_pdf')}: {alerta}")
    if alertas:
        linhas.extend(f"- {alerta}" for alerta in alertas)
    else:
        linhas.append("- Nenhum alerta técnico relevante.")

    linhas += [
        "",
        "## 7. Auditoria Técnica",
        f"- Páginas candidatas a decisões: {aud.get('paginas_candidatas_decisoes', 0)}",
        f"- Páginas candidatas a ponto: {aud.get('paginas_candidatas_ponto', 0)}",
        f"- Páginas candidatas a holerites: {aud.get('paginas_candidatas_holerites', 0)}",
        f"- Páginas candidatas a cálculos: {aud.get('paginas_candidatas_calculos', 0)}",
        f"- Alertas emitidos: {aud.get('alertas_emitidos', 0)}",
    ]
    return "\n".join(linhas)


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
            num = re.sub(r"\.", "", m.group(2))  # dígitos puros
            # Reagrupar com ponto como separador de milhar (ex: 113880 → 113.880)
            if len(num) > 3:
                num = ".".join(
                    [num[max(0, i-3):i] for i in range(len(num), 0, -3)][::-1]
                )
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

def buscar_secoes(doc: fitz.Document, toc: list, txt: str, textos_paginas: list[str] | None = None) -> dict:
    """
    Quando o TOC está disponível (PDFs PJe), cada documento é identificado
    individualmente com tipo, data e páginas exatas.
    Retorna sec["documentos_decisao"] como lista ordenada de documentos,
    além das chaves agrupadas por categoria para compatibilidade.
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
    DECISOES_CATS = {"sentenca", "acordao", "embargos", "decisao"}
    npags = len(doc)
    sec = {"documentos_decisao": []}
    textos_paginas = textos_paginas or []

    def texto_intervalo(p0: int, p1: int) -> str:
        blocos = []
        for i in range(p0, min(p1 + 1, npags)):
            if i < len(textos_paginas):
                blocos.append(textos_paginas[i])
            else:
                blocos.append(doc[i].get_text())
        return "\n".join(blocos)

    # Títulos a excluir — não são decisões, são notificações ou petições
    EXCLUIR = re.compile(
        r"^Intima[çc][aã]o|^Manifesta[çc][aã]o|^Contrarraz|^Recurso|"
        r"^Agravo|^Certid[aã]o|^Comprovante|^Guia|^Planilha|"
        r"^Impugna[çc][aã]o|^Petição|^Procura[çc][aã]o",
        re.IGNORECASE
    )
    # Tipos de decisão relevantes para liquidação
    DECISOES_RELEVANTES = {
        "Sentença", "Acórdão", "Embargos de Declaração", "Decisão"
    }

    if toc:
        from collections import defaultdict
        grupos = defaultdict(list)

        for idx, entry in enumerate(toc):
            _, titulo, pag_ini = entry
            # Extrair o nome do tipo do título (após "N. DATA - TIPO - hash")
            titulo_limpo = re.sub(r"^\d+\.\s+\d{2}/\d{2}/\d{4}\s+-\s+", "", titulo)
            titulo_limpo = re.sub(r"\s+-\s+[a-f0-9]{7,8}$", "", titulo_limpo, flags=re.IGNORECASE)

            # Excluir intimações, petições e outros documentos não-decisórios
            if EXCLUIR.search(titulo_limpo):
                continue

            tipo_cat = next(
                (cat for tipo_str, cat in MAPA_TIPOS.items()
                 if tipo_str.lower() in titulo_limpo.lower()),
                None
            )
            if not tipo_cat: continue

            pag_fim = (toc[idx+1][2] - 1) if idx+1 < len(toc) else npags
            p0, p1 = pag_ini - 1, pag_fim - 1  # 0-based
            texto_doc = texto_intervalo(p0, p1)

            # Data do título do TOC (confiável)
            data_m = re.search(r"(\d{2}/\d{2}/\d{4})", titulo)
            data = data_m.group(1) if data_m else None

            if tipo_cat in DECISOES_CATS:
                # Embargos: excluir petições da parte (sem assinatura de juiz)
                if tipo_cat == "embargos":
                    if not re.search(r"Juiz\b|Desembargador|Ministro", texto_doc, re.IGNORECASE):
                        continue  # petição da parte, não decisão judicial

                # Guardar documento individual — usado por extrair_decisoes
                sec["documentos_decisao"].append({
                    "tipo_cat": tipo_cat,
                    "tipo_label": {
                        "sentenca": "Sentença",
                        "acordao":  "Acórdão",
                        "embargos": "Embargos de Declaração",
                        "decisao":  "Decisão",
                    }.get(tipo_cat, tipo_cat.capitalize()),
                    "titulo":   titulo,
                    "data":     data,
                    "texto":    texto_doc,
                })
                grupos[tipo_cat].append(f"\n--- {titulo} ---\n{texto_doc}")
            elif tipo_cat in ("ficha", "ponto"):
                grupos[tipo_cat].append(texto_doc)

        for cat, blocos in grupos.items():
            sec[cat] = "\n\n".join(blocos)

    # Fallback textual para PDFs sem TOC
    if not sec["documentos_decisao"]:
        linhas = txt.split("\n")
        padroes = {
            "sentenca": r"VISTOS[,\s]+RELATADOS|VISTOS[,\s]+ETC",
            "acordao":  r"A\s*C\s*[OÓ]\s*R\s*D\s*[AÃ]\s*O",
        }
        for nome, p in padroes.items():
            if nome in sec: continue
            for i in range(len(linhas)-1, -1, -1):
                if re.search(p, linhas[i], re.IGNORECASE):
                    sec[nome] = "\n".join(linhas[i:i+600])
                    break

    # Ficha e ponto via busca textual se não vieram do TOC
    for nome, padrao in [
        ("ficha", r"FICHA FINANCEIRA|CONTRACHEQUE|HOLERITE"),
        ("ponto", r"CART[AÃ]O DE PONTO|ESPELHO DE PONTO"),
    ]:
        if not sec.get(nome):
            linhas = txt.split("\n")
            for i, l in enumerate(linhas):
                if re.search(padrao, l, re.IGNORECASE):
                    sec[nome] = "\n".join(linhas[i:i+600]); break

    return sec
# ── 1.3 Extrair decisões (sem IA — extração literal de texto) ─────────────────

MARCADORES_TIPO = {
    "Sentença":              r"S\s*E\s*N\s*T\s*E\s*N\s*[CÇ]\s*A|VISTOS[,\s]+RELATADOS",
    "Acórdão":               r"A\s*C\s*[OÓ]\s*R\s*D\s*[AÃ]\s*O",
    "Embargos de Declaração":r"EMBARGOS\s+DE\s+DECLARA[CÇ][AÃ]O",
}
MARCADORES_DISPOSITIVO = [
    r"ISSO\s+POSTO", r"ISTO\s+POSTO", r"ANTE\s+O\s+EXPOSTO",
    r"DIANTE\s+DO\s+EXPOSTO", r"PELO\s+EXPOSTO",
    r"DECIDO\s*[:;]", r"DECIDE-SE", r"JULGO",
    r"ACORDAM\s+os",       # acórdão TRT
    r"^(III\s*\)\s*)?CONCLUSÃO",  # decisões TST/TRT monocráticas
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

def extrair_decisao_de_doc(doc_info: dict) -> dict:
    """
    Extrai dados de uma decisão a partir de um documento individual do TOC.
    O tipo e a data já são conhecidos — só precisa localizar o dispositivo.
    """
    tipo_label = doc_info["tipo_label"]
    data       = doc_info["data"]  # data confiável do TOC
    texto      = doc_info["texto"]
    linhas     = texto.split("\n")
    bloco      = texto

    # ── Dispositivo ──────────────────────────────────────────────────────────────
    # Para sentenças longas, priorizar marcadores condenatórios sobre os introdutórios
    # Ordem de preferência: Ante o exposto > Condeno > ISSO POSTO > ...
    MARCADORES_CONDENATORIO = [
        r"Ante\s+o\s+exposto[,\.]",          # sentença TRT — resume a condenação
        r"(?:^|\n)\s*Condeno\b",              # condenação direta
        r"(?:^|\n)\s*JULGO\s+PROCED",
        r"NEGO\s+PROVIMENTO",                 # acórdão — nega o recurso
        r"DAR\s+PARCIAL\s+PROVIMENTO",        # acórdão — provimento parcial
        r"DAR\s+PROVIMENTO",                  # acórdão — provimento total
        r"ACORDAM\s+os",                      # acórdão — início do decisum
        r"Nego\s+seguimento",                 # TST/TRT — inadmite recurso
        r"denego\s+seguimento",               # TST — nega AIRR
        r"^(III\s*\)\s*)?CONCLUSÃO",          # TST monocrático
        r"Recebo\s+o\s+recurso",              # despacho de admissão
    ]
    MARCADORES_INTRO = [                      # usados só se nada acima for encontrado
        r"ISSO\s+POSTO", r"ISTO\s+POSTO",
        r"DIANTE\s+DO\s+EXPOSTO", r"PELO\s+EXPOSTO",
        r"DECIDO\s*[:;]", r"DECIDE-SE",
    ]

    dispositivo = ""
    inicio_disp = None

    # 1ª passagem: marcadores condenatórios (mais precisos)
    for j, l in enumerate(linhas):
        if any(re.search(p, l, re.IGNORECASE | re.MULTILINE) for p in MARCADORES_CONDENATORIO):
            inicio_disp = j
            break

    # 2ª passagem: marcadores introdutórios (fallback)
    if inicio_disp is None:
        for j, l in enumerate(linhas):
            if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_INTRO):
                inicio_disp = j
                break

    if inicio_disp is not None:
        linhas_disp = []
        for l in linhas[inicio_disp:]:
            linhas_disp.append(l)
            if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_FIM_DISPOSITIVO):
                break
        dispositivo = "\n".join(linhas_disp).strip()
    else:
        linhas_uteis = [l for l in linhas if l.strip() and not re.search(
            r"^Fls\.|^Documento assinado|^https?://|^Número do (processo|documento)|^Certificado",
            l.strip()
        )]
        dispositivo = "\n".join(linhas_uteis).strip()

    # ── Resultado — baseado no tipo e conteúdo do documento ──────────────────────
    tipo_cat = doc_info.get("tipo_cat", "")
    titulo   = doc_info.get("titulo", "").lower()

    if tipo_cat == "decisao":
        if re.search(r"Recebo\s+o\s+recurso|recurso.*recebido", bloco, re.IGNORECASE):
            resultado = "despacho — recurso admitido"
        elif re.search(r"denego\s+seguimento|negado\s+seguimento|intranscend", bloco, re.IGNORECASE):
            resultado = "recurso não admitido (TST)"  # verifica antes de "Nego seguimento"
        elif re.search(r"Nego\s+seguimento|não\s+admito|deserção", bloco, re.IGNORECASE):
            resultado = "recurso não admitido"
        else:
            resultado = "decisão interlocutória"

    elif tipo_cat == "embargos":
        if re.search(r"NEGO\s+PROVIMENTO|embargos.*rejeit|não\s+merece\s+prosperar", bloco, re.IGNORECASE):
            resultado = "embargos rejeitados"
        elif re.search(r"ACOLHO|acolhidos", bloco, re.IGNORECASE):
            resultado = "embargos acolhidos"
        else:
            resultado = "embargos parcialmente acolhidos"

    elif tipo_cat == "acordao":
        if re.search(r"DAR\s+PARCIAL\s+PROVIMENTO", bloco, re.IGNORECASE):
            resultado = "parcialmente provido"
        elif re.search(r"DAR\s+PROVIMENTO\b", bloco, re.IGNORECASE):
            resultado = "provido"
        elif re.search(r"NEGAR\s+PROVIMENTO|NEGO\s+PROVIMENTO|unanimidade.*negar", bloco, re.IGNORECASE):
            resultado = "negado provimento"
        else:
            resultado = "parcialmente provido"

    else:  # sentenca — inclui julgamento de embargos de declaração
        # Sentença sobre embargos: tipo_cat é "sentenca" mas trata embargos
        if re.search(r"embargos\s+de\s+declara", bloco, re.IGNORECASE) and \
           re.search(r"NEGO\s+PROVIMENTO|não\s+merece\s+prosperar|rejeito\s+os\s+embargos", bloco, re.IGNORECASE):
            resultado = "embargos rejeitados"
        elif re.search(r"embargos\s+de\s+declara", bloco, re.IGNORECASE) and \
             re.search(r"ACOLHO|acolhidos", bloco, re.IGNORECASE):
            resultado = "embargos acolhidos"
        elif re.search(r"JULGO\s+IMPROCEDENTE|totalmente\s+improcedente", bloco, re.IGNORECASE):
            resultado = "improcedente"
        elif re.search(r"PROCEDENTE[^S]\b|integralmente\s+procedente", bloco, re.IGNORECASE):
            resultado = "procedente"
        else:
            resultado = "parcialmente procedente"

    # Verbas — lista pré-definida
    verbas = [v for v in VERBAS_CONHECIDAS
              if re.search(re.escape(v), bloco, re.IGNORECASE)]

    return {
        "tipo":                 tipo_label,
        "data":                 data,
        "dispositivo":          dispositivo or "(dispositivo não localizado)",
        "resultado_reclamante": resultado,
        "verbas_deferidas":     verbas,
    }


def extrair_decisoes(secs: dict) -> list[dict]:
    """
    Extrai todas as decisões do processo.
    Quando o TOC está disponível (secs["documentos_decisao"]),
    processa cada documento individualmente — tipo e data já são conhecidos.
    Fallback: busca textual no texto concatenado.
    """
    documentos = secs.get("documentos_decisao", [])

    if documentos:
        # TOC disponível — processar cada documento individualmente
        return [extrair_decisao_de_doc(d) for d in documentos]

    # Fallback: texto concatenado (PDFs sem TOC)
    txt = "\n\n".join(filter(None, [
        secs.get("sentenca"), secs.get("acordao"),
        secs.get("embargos"), secs.get("decisao"),
    ]))
    if not txt:
        return []

    decisoes = []
    linhas = txt.split("\n")
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        tipo_encontrado = None
        for tipo, padrao in MARCADORES_TIPO.items():
            if re.search(padrao, linha, re.IGNORECASE):
                tipo_encontrado = tipo
                break
        if not tipo_encontrado:
            i += 1; continue

        janela = linhas[i:i+600]
        bloco  = "\n".join(janela)
        m_data = re.search(r"\d{2}/\d{2}/\d{4}", bloco)
        data   = m_data.group() if m_data else None

        inicio_disp = None
        for j, l in enumerate(janela):
            if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_DISPOSITIVO):
                inicio_disp = j; break

        dispositivo = ""
        if inicio_disp is not None:
            linhas_disp = []
            for l in janela[inicio_disp:]:
                linhas_disp.append(l)
                if any(re.search(p, l, re.IGNORECASE) for p in MARCADORES_FIM_DISPOSITIVO):
                    break
            dispositivo = "\n".join(linhas_disp).strip()

        resultado = "parcialmente procedente"
        if re.search(r"JULGO\s+IMPROCEDENTE|NEGO\s+PROVIMENTO", bloco, re.IGNORECASE):
            resultado = "improcedente / negado provimento"
        elif re.search(r"DAR\s+PARCIAL\s+PROVIMENTO", bloco, re.IGNORECASE):
            resultado = "parcialmente provido"

        verbas = [v for v in VERBAS_CONHECIDAS if re.search(re.escape(v), bloco, re.IGNORECASE)]

        decisoes.append({
            "tipo": tipo_encontrado, "data": data,
            "dispositivo": dispositivo or "(dispositivo não localizado)",
            "resultado_reclamante": resultado, "verbas_deferidas": verbas,
        })
        i += 400

    return decisoes


# ── 1.4 Ficha financeira (PyMuPDF tabelas — sem IA) ───────────────────────────

def extrair_ficha(doc: fitz.Document, textos_paginas: list[str] | None = None) -> dict:
    """
    Extrai ficha financeira usando detecção de tabelas do PyMuPDF.
    Sem IA — dados tabulares são determinísticos.
    """
    rubricas_set = set()
    competencias = []

    textos_paginas = textos_paginas or []
    for idx, pagina in enumerate(doc):
        texto_pag = textos_paginas[idx] if idx < len(textos_paginas) else pagina.get_text()
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

def extrair_ponto(doc: fitz.Document, textos_paginas: list[str] | None = None) -> dict:
    """
    Extrai espelho de ponto usando detecção de tabelas do PyMuPDF.
    Sem IA — dados tabulares são determinísticos.
    """
    registros = []
    DIAS = {"SEG","TER","QUA","QUI","SEX","SÁB","SAB","DOM"}

    textos_paginas = textos_paginas or []
    for idx, pagina in enumerate(doc):
        texto_pag = textos_paginas[idx] if idx < len(textos_paginas) else pagina.get_text()
        if not re.search(
            r"CART[AÃ]O DE PONTO|ESPELHO DE PONTO|REGISTRO DE PONTO",
            texto_pag, re.IGNORECASE
        ):
            continue

        registros_antes = len(registros)
        try:
            tabelas = pagina.find_tables()
        except Exception:
            tabelas = []

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

        if len(registros) == registros_antes:
            registros.extend(_extrair_ponto_texto_livre(texto_pag))

    if not registros:
        return {"erro": "Ponto não localizado ou sem tabelas detectáveis no PDF"}

    return {"registros": registros}


def _extrair_ponto_texto_livre(texto: str) -> list[dict]:
    """Fallback simples para OCR/texto corrido de espelho de ponto."""
    registros = []
    dias = r"SEG|TER|QUA|QUI|SEX|S[ÁA]B|SAB|DOM"
    for linha in (texto or "").splitlines():
        if not re.search(r"\d{2}/\d{2}/\d{4}", linha):
            continue
        horarios = re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", linha)
        if not horarios:
            continue
        data = re.search(r"\d{2}/\d{2}/\d{4}", linha).group(0)
        dia_m = re.search(rf"\b({dias})\b", linha, re.IGNORECASE)
        obs_m = re.search(r"\b(DSR|FALTA|FERIADO|FOLGA|AFASTAMENTO|F[ÉE]RIAS|ATESTADO)\b.*", linha, re.IGNORECASE)
        registros.append({
            "data": data,
            "dia_semana": dia_m.group(1).upper().replace("Á", "A") if dia_m else "",
            "entradas_saidas": horarios,
            "horas_trabalhadas": "",
            "horas_extras": "",
            "observacao": obs_m.group(0).strip() if obs_m else "",
            "fonte": "texto_ocr",
        })
    return registros
