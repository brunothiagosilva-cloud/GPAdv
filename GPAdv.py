# -*- coding: utf-8 -*-
# GPAdv_Web.py  —  "Meu Controle Jurídico" (Web / Supabase)
# ------------------------------------------------------------------------------------

import os
import json
import re
import imaplib
import email
import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from cryptography.fernet import Fernet

# --- Dependências Opcionais ---
try:
    import openai
except ImportError:
    openai = None

# ---------------- Configurações da Página ----------------
st.set_page_config(
    page_title="GPAdv - Meu Controle Jurídico",
    page_icon="⚖️",
    layout="wide"
)

# ---------------- Inicialização do Supabase ----------------
@st.cache_resource
def init_connection() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except KeyError:
        st.error("⚠️ Chaves do Supabase não encontradas. Configure o arquivo .streamlit/secrets.toml")
        st.stop()

supabase = init_connection()

# ---------------- Config & Paths Locais (E-mail/Cripto) ----------------
APP_VERSION = "25.0 (Cloud)"
INSTALL_DIR = Path("C:/GerenciadorProcessos")
DATA_DIR = INSTALL_DIR / "data"

for p in (INSTALL_DIR, DATA_DIR):
    p.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = INSTALL_DIR / "config.json"
KEY_PATH = INSTALL_DIR / "secret.key"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

DEFAULT_CONFIG = {
    "email": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "username": "",
        "password_enc": "",
        "folder": "INBOX"
    },
    "openai": {
        "enabled": False,
        "api_key": "",
        "model": "gpt-4o-mini"
    }
}

def get_fernet():
    try:
        if not KEY_PATH.exists():
            key = Fernet.generate_key()
            KEY_PATH.write_bytes(key)
        else:
            key = KEY_PATH.read_bytes()
        return Fernet(key)
    except:
        return None

def decrypt_pw(pw_enc: str) -> str:
    f = get_fernet()
    if not f or not pw_enc: return pw_enc
    try: return f.decrypt(pw_enc.encode("utf-8")).decode("utf-8")
    except: return pw_enc

def load_config():
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg
    except:
        return DEFAULT_CONFIG

CONFIG = load_config()

# ---------------- Funções de Banco de Dados (Supabase) ----------------
def load_data():
    try:
        response = supabase.table("processos").select("*").order("id", desc=True).execute()
        if response.data:
            return pd.DataFrame(response.data)
        else:
            # Retorna DataFrame vazio com as colunas corretas se não houver dados
            return pd.DataFrame(columns=["id", "numero", "tribunal", "parte", "situacao", "prazo", "observacoes", "marcado", "cor_card", "notif_data"])
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        return pd.DataFrame()

# ---------------- Lógica de Negócios ----------------
def read_publications_from_email():
    cfg = CONFIG.get("email", {})
    if not cfg.get("username") or not cfg.get("password_enc"):
        st.warning("Configure as credenciais de e-mail no arquivo config.json (C:/GerenciadorProcessos).")
        return

    try:
        M = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        M.login(cfg["username"], decrypt_pw(cfg["password_enc"]))
        M.select(f'"{cfg["folder"]}"')
        
        typ, data = M.search(None, '(UNSEEN)')
        if typ != 'OK' or not data[0]:
            st.info("Nenhuma mensagem não lida encontrada.")
            M.logout()
            return

        matched = set()
        regex = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
        
        for num in data[0].split():
            typ, msg_data = M.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            body = msg.get_payload(decode=True).decode("utf-8", "ignore") if not msg.is_multipart() else ""
            matched.update(regex.findall(body))
        
        M.logout()
        
        if matched:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d")
            for proc_num in matched:
                # Atualiza no Supabase
                supabase.table("processos").update(
                    {"marcado": "📩", "notif_data": now_str}
                ).eq("numero", proc_num).execute()
                
            st.success(f"{len(matched)} publicações encontradas e marcadas!")
        else:
            st.info("Nenhum número de processo encontrado nos e-mails não lidos.")
            
    except Exception as e:
        st.error(f"Erro IMAP: {e}")

def generate_piece(proc_num):
    if not openai or not CONFIG.get("openai", {}).get("enabled"):
        st.info(f"Mockup: Petição Inicial gerada para {proc_num} (OpenAI desabilitado ou não instalado).")
        return
        
    try:
        openai.api_key = CONFIG["openai"]["api_key"] or OPENAI_API_KEY
        resp = openai.ChatCompletion.create(
            model=CONFIG["openai"]["model"],
            messages=[{"role":"user", "content": f"Elabore uma Petição Inicial genérica estruturada para o processo {proc_num}."}]
        )
        texto = resp['choices'][0]['message']['content']
        
        now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        # Insere histórico no Supabase
        supabase.table("historico_pecas").insert({
            "numero_processo": proc_num, 
            "data_hora": now, 
            "descricao": "Petição Gerada via AI"
        }).execute()
                         
        st.success(f"Peça gerada para {proc_num} com sucesso!")
        with st.expander("Ver Peça Gerada", expanded=True):
            st.write(texto)
            
    except Exception as e:
        st.error(f"Erro na OpenAI: {e}")

# ---------------- Interface Streamlit ----------------
st.title(f"⚖️ GPAdv - Meu Controle Jurídico v{APP_VERSION}")

# Layout principal: Sidebar e Conteúdo
with st.sidebar:
    st.header("Ações Rápidas")
    
    with st.form("add_process_form", clear_on_submit=True):
        st.subheader("➕ Adicionar Processo")
        new_numero = st.text_input("Número do Processo")
        new_parte = st.text_input("Nome da Parte")
        submitted = st.form_submit_button("Salvar")
        
        if submitted and new_numero:
            # Insere novo processo no Supabase
            supabase.table("processos").insert({
                "numero": new_numero, 
                "tribunal": "TJ-SP", 
                "parte": new_parte, 
                "situacao": "Em Andamento", 
                "prazo": "", 
                "observacoes": "", 
                "marcado": "", 
                "cor_card": "", 
                "notif_data": ""
            }).execute()
            
            st.success("Adicionado com sucesso!")
            st.rerun()

    st.divider()
    
    if st.button("📧 Ler Publicações (E-mail)", use_container_width=True):
        with st.spinner("Conectando ao e-mail..."):
            read_publications_from_email()
            st.rerun()

# Área Principal de Dados
df = load_data()

# Filtro de Busca
busca = st.text_input("🔍 Pesquisar Processo:", "")
if busca and not df.empty:
    df = df[df.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)]

st.write("### Grid de Processos")
st.caption("Edite diretamente na tabela abaixo. Para excluir, selecione a linha na lateral esquerda e aperte a lixeira (canto superior da tabela).")

if df.empty:
    st.info("Nenhum processo encontrado. Adicione um novo processo pelo menu lateral.")
else:
    # Renderiza a tabela editável
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "id": None, # Esconde a coluna ID do banco
            "cor_card": None,
            "notif_data": None
        },
        key="process_editor"
    )

    # Captura as edições e atualiza o Supabase
    if st.session_state.get("process_editor"):
        changes = st.session_state["process_editor"]
        needs_rerun = False
        
        # 1. Processar Edições Inline (Updates)
        if changes.get("edited_rows"):
            for row_idx, col_changes in changes["edited_rows"].items():
                proc_id = df.iloc[row_idx]["id"]
                
                # Atualiza os campos modificados no Supabase
                supabase.table("processos").update(col_changes).eq("id", proc_id).execute()
                
            needs_rerun = True
            
        # 2. Processar Deleções (Deletes)
        if changes.get("deleted_rows"):
            for row_idx in changes["deleted_rows"]:
                proc_id = df.iloc[row_idx]["id"]
                
                # Deleta o registro no Supabase
                supabase.table("processos").delete().eq("id", proc_id).execute()
                
            needs_rerun = True

        if needs_rerun:
            st.success("Alterações salvas na nuvem!")
            st.rerun()

# Seção de Geração de Peças
st.divider()
st.write("### 🧠 Gerar Peça com IA")
col1, col2 = st.columns([1, 3])

with col1:
    if not df.empty:
        processos_lista = df["numero"].tolist()
        processo_selecionado = st.selectbox("Selecione o Processo:", processos_lista)
        
        if st.button("Gerar Petição Inicial", type="primary", use_container_width=True):
            with st.spinner("A Inteligência Artificial está redigindo a peça..."):
                generate_piece(processo_selecionado)
    else:
        st.info("Adicione processos para utilizar a geração de peças.")
