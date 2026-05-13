"""AI integration and criteria context assembly for SinteseProc."""

import json
import re
import time
from collections.abc import Callable

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
    if verbas:
        partes.append("Verbas identificadas: " + ", ".join(verbas))
    partes.append("Dispositivo:\n" + (decisao.get("dispositivo") or "(dispositivo não localizado)"))
    return "\n".join(partes)


def montar_contexto_criterios(
    decisoes: list[dict] | None,
    secs: dict | None,
    texto_completo: str,
    limite: int = 30000,
) -> str:
    """Monta o texto enviado à IA para critérios de liquidação.

    Prioriza todos os dispositivos extraídos em ordem. Quando não há decisões,
    usa seções determinísticas e, por último, o fim do texto completo.
    """
    decisoes = decisoes or []
    secs = secs or {}

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
    params = dict(
        model=modelo, stream=False,
        messages=[
            {"role":"system","content":(
                "Você é perito contábil trabalhista brasileiro especializado em liquidação de sentença no TRT4.\n"
                + REGRAS_ANALISE
                + (f"\n\n## Base de referência jurídica\n{base}" if base else "")
                + "\n\nResponda APENAS com JSON válido, sem texto antes/depois, sem ```json```.")},
            {"role":"user","content":f"{instrucao}\n\nTEXTO:\n{txt[:limite]}"},
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
