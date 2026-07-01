# -*- coding: utf-8 -*-
# GPAdv_Web.py  —  "Meu Controle Jurídico" (Web / Streamlit)
# ------------------------------------------------------------------------------------

import os
import json
import re
import imaplib
import email
import datetime
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st
from cryptography.fernet import Fernet

# --- Dependências Opcionais ---
try:
    import openai
except ImportError:
    openai = None

# ---------------- Configurações da Página ----------------
st.set_page_config(
    page_title="Meu Controle Jurídico",
    page_icon="⚖️",
    layout="wide"
)

# ---------------- Config & Paths ----------------
APP_VERSION = "24.0 (Web)"
INSTALL_DIR = Path("C:/GerenciadorProcessos")
DATA_DIR = INSTALL_DIR / "data"

for p in (INSTALL_DIR, DATA_DIR):
    p.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "processos.db"
CONFIG_PATH = INSTALL_DIR / "config.json"
KEY_PATH = INSTALL_DIR / "secret.key"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ---------------- Banco de Dados (SQLite) ----------------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT,
                tribunal TEXT,
                parte TEXT,
                situacao TEXT,
                prazo TEXT,
                observacoes TEXT,
                marcado TEXT,
                cor_card TEXT,
                notif_data TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico_pecas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_processo TEXT,
                data_hora TEXT,
                descricao TEXT,
                arquivo TEXT
            )
        """)
        conn.commit()

def execute_db_query(query, params=()):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
    except Exception as e:
        st.error(f"Erro no banco de dados: {e}")

def load_data():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM processos", conn)
    return df

init_db()

# ---------------- Config & Cripto ----------------
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

# ---------------- Lógica de Negócios ----------------

def read_publications_from_email():
    cfg = CONFIG.get("email", {})
    if not cfg.get("username") or not cfg.get("password_enc"):
        st.warning("Configure as credenciais de e-mail no arquivo config.json.")
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
                execute_db_query("UPDATE processos SET marcado = '📩', notif_data = ? WHERE numero = ?", (now_str, proc_num))
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
        execute_db_query("INSERT INTO historico_pecas (numero_processo, data_hora, descricao) VALUES (?, ?, ?)", 
                         (proc_num, now, "Petição Gerada via AI"))
                         
        st.success(f"Peça gerada para {proc_num} com sucesso!")
        with st.expander("Ver Peça Gerada", expanded=True):
            st.write(texto)
            
    except Exception as e:
        st.error(f"Erro na OpenAI: {e}")

# ---------------- Interface Streamlit ----------------

st.title(f"⚖️ Meu Controle Jurídico v{APP_VERSION}")

# Layout principal: Sidebar e Conteúdo
with st.sidebar:
    st.header("Ações Rápidas")
    
    with st.form("add_process_form", clear_on_submit=True):
        st.subheader("➕ Adicionar Processo")
        new_numero = st.text_input("Número do Processo")
        new_parte = st.text_input("Nome da Parte")
        submitted = st.form_submit_button("Salvar")
        if submitted and new_numero:
            execute_db_query(
                "INSERT INTO processos (numero, tribunal, parte, situacao, prazo, observacoes, marcado, cor_card, notif_data) VALUES (?, 'TJ-SP', ?, 'Em Andamento', '', '', '', '', '')",
                (new_numero, new_parte)
            )
            st.success("Adicionado!")
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
if busca:
    df = df[df.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)]

st.write("### Grid de Processos")
st.caption("Edite diretamente na tabela abaixo. Para excluir, selecione a linha na lateral esquerda e aperte a lixeira no cabeçalho.")

# O st.data_editor substitui perfeitamente o Treeview com edição inline
edited_df = st.data_editor(
    df,
    use_container_width=True,
    num_rows="dynamic",
    hide_index=True,
    column_config={
        "id": None, # Esconde o ID interno
        "cor_card": None,
        "notif_data": None
    },
    key="process_editor"
)

# Sincronização automática das edições do data_editor com o SQLite
if st.session_state.get("process_editor"):
    changes = st.session_state["process_editor"]
    needs_rerun = False
    
    # 1. Processar Edições Inline
    if changes.get("edited_rows"):
        for row_idx, col_changes in changes["edited_rows"].items():
            proc_id = df.iloc[row_idx]["id"]
            for col_name, new_val in col_changes.items():
                query = f"UPDATE processos SET {col_name} = ? WHERE id = ?"
                execute_db_query(query, (new_val, int(proc_id)))
        needs_rerun = True
        
    # 2. Processar Deleções
    if changes.get("deleted_rows"):
        for row_idx in changes["deleted_rows"]:
            proc_id = df.iloc[row_idx]["id"]
            execute_db_query("DELETE FROM processos WHERE id = ?", (int(proc_id),))
        needs_rerun = True

    if needs_rerun:
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