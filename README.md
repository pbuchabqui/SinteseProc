# Síntese de Decisões Trabalhista

Ferramenta para extrair dados de processos TRT e gerar documento Word para liquidação.

---

## Como instalar e usar (passo a passo)

### 1. Obter chave Groq (gratuita)

1. Acesse https://console.groq.com/
2. Crie conta (pode usar login do Google)
3. Clique em **API Keys → Create API Key**
4. Copie a chave — começa com `gsk_`

---

### 2. Criar repositório no GitHub

1. Acesse https://github.com/new
2. Nome do repositório: `sintese-liquidacao`
3. Marque **Private** (recomendado)
4. Clique em **Create repository**

---

### 3. Subir os arquivos

Na página do repositório recém-criado, clique em **uploading an existing file**.

Suba estes arquivos:
- `app.py`
- `requirements.txt`
- `packages.txt`

Clique em **Commit changes**.

---

### 4. Criar conta no Streamlit Cloud

1. Acesse https://share.streamlit.io/
2. Clique em **Continue with GitHub**
3. Autorize o acesso

---

### 5. Fazer o deploy

1. Clique em **New app**
2. Selecione seu repositório `sintese-liquidacao`
3. Branch: `main`
4. Main file path: `app.py`
5. Clique em **Deploy!**

Aguarde ~2 minutos para o deploy terminar.

---

### 6. Configurar a chave Groq

No painel do Streamlit Cloud, com o app aberto:

1. Clique em **Settings** (ícone de engrenagem)
2. Clique em **Secrets**
3. Cole exatamente isto (substituindo pela sua chave):

```
GROQ_API_KEY = "gsk_SUACHAVEAQUI"
GROQ_MODEL = "llama-3.3-70b-versatile"
```

4. Clique em **Save**
5. O app reinicia automaticamente

---

### 7. Usar o sistema

1. Acesse a URL do seu app (ex: `https://seu-usuario-sintese-liquidacao.streamlit.app`)
2. Faça upload do PDF do processo
3. Marque o que deseja extrair
4. Clique em **Processar**
5. Aguarde (~1 minuto)
6. Clique em **Baixar Word**

---

## Atualizar o sistema

Quando houver nova versão do `app.py`:

1. No GitHub, abra o arquivo `app.py`
2. Clique no ícone de lápis (editar)
3. Cole o novo conteúdo
4. Clique em **Commit changes**

O Streamlit Cloud atualiza automaticamente em ~1 minuto.

---

## Limites do Groq (plano gratuito)

- 14.400 requisições por dia
- Para este sistema: ~3 requisições por processo
- Equivale a ~4.800 processos por dia — mais que suficiente
