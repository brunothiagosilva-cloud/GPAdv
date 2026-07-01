# -*- coding: utf-8 -*-
# GPAdv_Web.py  —  "Meu Controle Jurídico" (Web / Supabase Enterprise)
# ------------------------------------------------------------------------------------

import os
import json
import re
import imaplib
import email
import datetime
import io
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
    page_title="GPAdv - Sistema de Gestão Jurídica",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------- Inicialização do Supabase ----------------
@st.cache_resource
def init_connection() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except KeyError:
        st.error("⚠️ Chaves do Supabase não encontradas. Verifique o arquivo .streamlit/secrets.toml ou os Secrets do Streamlit Cloud.")
        st.stop()

supabase = init_connection()

# ---------------- Config & Paths Locais (E-mail/Cripto) ----------------
APP_VERSION = "26.0 (Enterprise Cloud)"
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
    except Exception as e:
        st.error(f"Erro de criptografia: {e}")
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

# ---------------- Gestão de Estado da Sessão (Autenticação) ----------------
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user_email' not in st.session_state:
    st.session_state['user_email'] = ""

def login(email_input, password_input):
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email_input,
            "password": password_input
        })
        if response.user:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = response.user.email
            st.success("Login realizado com sucesso!")
            st.rerun()
    except Exception as e:
        st.error(f"Falha na autenticação: Verifique suas credenciais. (Erro: {e})")

def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""
    st.rerun()

# ---------------- Funções de Banco de Dados (Supabase) ----------------
def load_data():
    try:
        response = supabase.table("processos").select("*").order("id", desc=True).execute()
        if response.data:
            return pd.DataFrame(response.data)
        else:
            return pd.DataFrame(columns=["id", "numero", "tribunal", "parte", "situacao", "prazo", "observacoes", "marcado", "cor_card", "notif_data"])
    except Exception as e:
        st.error(f"Erro ao carregar dados do banco: {e}")
        return pd.DataFrame()

# ---------------- Funções de Importação e Exportação (Excel) ----------------
def export_to_excel(df: pd.DataFrame) -> bytes:
    """Gera um arquivo Excel em memória a partir do DataFrame."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Processos_GPAdv')
        workbook = writer.book
        worksheet = writer.sheets['Processos_GPAdv']
        
        # Formatação básica de colunas
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 20)
            
    return output.getvalue()

def import_from_excel(uploaded_file):
    """Lê um arquivo Excel e insere os registros no Supabase."""
    try:
        df_import = pd.read_excel(uploaded_file, engine='openpyxl')
        
        # Validação básica de colunas esperadas
        colunas_esperadas = ["numero", "tribunal", "parte", "situacao", "prazo"]
        for col in colunas_esperadas:
            if col not in df_import.columns:
                st.error(f"A coluna '{col}' está ausente na planilha. O formato está incorreto.")
                return False
                
        # Preparar dados para inserção (remove NaNs que quebram o JSON do Supabase)
        df_import = df_import.fillna("")
        registros = df_import.to_dict(orient="records")
        
        # Filtra apenas as colunas que pertencem ao banco de dados para evitar erro de schema
        registros_limpos = []
        for reg in registros:
            registros_limpos.append({
                "numero": str(reg.get("numero", "")),
                "tribunal": str(reg.get("tribunal", "TJ-SP")),
                "parte": str(reg.get("parte", "")),
                "situacao": str(reg.get("situacao", "Em Andamento")),
                "prazo": str(reg.get("prazo", "")),
                "observacoes": str(reg.get("observacoes", "")),
                "marcado": str(reg.get("marcado", "")),
                "cor_card": str(reg.get("cor_card", "")),
                "notif_data": str(reg.get("notif_data", ""))
            })
            
        # Inserção em lote (Batch Insert)
        if registros_limpos:
            supabase.table("processos").insert(registros_limpos).execute()
            return True
            
    except Exception as e:
        st.error(f"Erro ao processar o arquivo Excel: {e}")
        return False

# ---------------- Lógica de Negócios (Email & IA) ----------------
def read_publications_from_email():
    cfg = CONFIG.get("email", {})
    if not cfg.get("username") or not cfg.get("password_enc"):
        st.warning("Configure as credenciais de e-mail localmente antes de usar este recurso.")
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
                supabase.table("processos").update(
                    {"marcado": "📩", "notif_data": now_str}
                ).eq("numero", proc_num).execute()
                
            st.success(f"{len(matched)} publicações encontradas e marcadas nos respectivos processos!")
        else:
            st.info("Nenhum número de processo correspondente encontrado nos e-mails.")
            
    except Exception as e:
        st.error(f"Falha de comunicação IMAP: {e}")

def generate_piece(proc_num):
    if not openai or not CONFIG.get("openai", {}).get("enabled"):
        st.info(f"Mockup Mode: Petição Inicial gerada simulada para o processo {proc_num} (Módulo OpenAI desabilitado).")
        return
        
    try:
        openai.api_key = CONFIG["openai"]["api_key"] or OPENAI_API_KEY
        resp = openai.ChatCompletion.create(
            model=CONFIG["openai"]["model"],
            messages=[{"role":"user", "content": f"Aja como um advogado sênior. Elabore uma Petição Inicial completa e estruturada para o processo {proc_num}. Não inclua resumos, gere a peça em sua totalidade."}]
        )
        texto = resp['choices'][0]['message']['content']
        
        now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        supabase.table("historico_pecas").insert({
            "numero_processo": proc_num, 
            "data_hora": now, 
            "descricao": "Petição Inicial (Gerada via IA)"
        }).execute()
                         
        st.success(f"Peça processual gerada para {proc_num} com sucesso!")
        with st.expander("Visualizar Documento Gerado", expanded=True):
            st.write(texto)
            st.download_button("Baixar Texto (TXT)", data=texto, file_name=f"Petição_{proc_num}.txt", mime="text/plain")
            
    except Exception as e:
        st.error(f"Erro na API da OpenAI: {e}")


# ==============================================================================
# RENDERIZAÇÃO DA INTERFACE PRINCIPAL
# ==============================================================================

if not st.session_state['authenticated']:
    # TELA DE LOGIN
    st.markdown("<h1 style='text-align: center; margin-top: 10vh;'>⚖️ GPAdv</h1>", unsafe_allow_html=True)
    st.markdown("<h4 style='text-align: center; color: gray;'>Sistema Corporativo de Gestão Jurídica</h4>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.container(border=True):
            st.subheader("Acesso ao Sistema")
            auth_email = st.text_input("E-mail corporativo")
            auth_senha = st.text_input("Senha", type="password")
            
            if st.button("Entrar", type="primary", use_container_width=True):
                if auth_email and auth_senha:
                    with st.spinner("Autenticando..."):
                        login(auth_email, auth_senha)
                else:
                    st.warning("Preencha todos os campos.")
else:
    # TELA DO SISTEMA AUTENTICADO
    
    # --- Sidebar ---
    with st.sidebar:
        st.markdown(f"👤 **Usuário:** {st.session_state['user_email']}")
        if st.button("Sair do Sistema", use_container_width=True):
            logout()
            
        st.divider()
        st.header("Ações Operacionais")
        
        # Adicionar Registro Manual
        with st.form("add_process_form", clear_on_submit=True):
            st.subheader("➕ Novo Processo")
            new_numero = st.text_input("Número do Processo")
            new_parte = st.text_input("Nome da Parte")
            submitted = st.form_submit_button("Salvar Registro", use_container_width=True)
            
            if submitted and new_numero:
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
                st.success("Processo cadastrado!")
                st.rerun()

        st.divider()
        
        # Leitor de Publicações
        if st.button("📧 Sincronizar Publicações (IMAP)", use_container_width=True):
            with st.spinner("Varrendo caixa de entrada..."):
                read_publications_from_email()
                st.rerun()

    # --- Área Principal ---
    st.title(f"⚖️ Dashboard de Processos")
    st.caption(f"Versão Corporativa {APP_VERSION}")

    # Carrega dados do Banco
    df = load_data()

    # Filtros e Métricas
    if not df.empty:
        met1, met2, met3 = st.columns(3)
        met1.metric("Total de Processos", len(df))
        met2.metric("Com Publicação Recente", len(df[df['marcado'] == '📩']))
        met3.metric("Em Andamento", len(df[df['situacao'].str.contains('Andamento', case=False, na=False)]))

    # --- Container de Ferramentas de Tabela ---
    with st.container(border=True):
        col_search, col_export, col_import = st.columns([2, 1, 1])
        
        with col_search:
            busca = st.text_input("🔍 Buscar em qualquer campo:", placeholder="Digite número, parte, tribunal...")
            if busca and not df.empty:
                df = df[df.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)]
                
        with col_export:
            st.write("<br>", unsafe_allow_html=True) # Espaçador
            if not df.empty:
                excel_bytes = export_to_excel(df)
                st.download_button(
                    label="📥 Exportar Excel",
                    data=excel_bytes,
                    file_name=f"Relatorio_Processos_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                
        with col_import:
            with st.popover("📤 Importar Excel", use_container_width=True):
                st.write("Selecione um arquivo .xlsx contendo as colunas padrão.")
                uploaded_file = st.file_uploader("", type=["xlsx"])
                if uploaded_file is not None:
                    if st.button("Confirmar Importação Lote", type="primary"):
                        with st.spinner("Inserindo registros no banco de dados..."):
                            sucesso = import_from_excel(uploaded_file)
                            if sucesso:
                                st.success("Importação concluída!")
                                st.rerun()

    # --- Tabela Editável ---
    st.write("### Base de Dados")
    st.caption("Edição Inline: Dê um duplo clique em qualquer célula para editar e pressione Enter. As alterações são sincronizadas em tempo real com a nuvem.")

    if df.empty:
        st.info("O banco de dados está vazio. Utilize o formulário lateral para adicionar registros ou importe uma planilha Excel.")
    else:
        edited_df = st.data_editor(
            df,
            use_container_width=True,
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "id": None, # Proteção do ID
                "cor_card": None,
                "notif_data": None,
                "numero": st.column_config.TextColumn("Número (CNJ)", required=True),
                "situacao": st.column_config.SelectboxColumn("Status", options=["Em Andamento", "Arquivado", "Suspenso", "Concluído"]),
            },
            key="process_editor"
        )

        # Captura de alterações (Commit no DB)
        if st.session_state.get("process_editor"):
            changes = st.session_state["process_editor"]
            needs_rerun = False
            
            if changes.get("edited_rows"):
                for row_idx, col_changes in changes["edited_rows"].items():
                    proc_id = df.iloc[row_idx]["id"]
                    supabase.table("processos").update(col_changes).eq("id", int(proc_id)).execute()
                needs_rerun = True
                
            if changes.get("deleted_rows"):
                for row_idx in changes["deleted_rows"]:
                    proc_id = df.iloc[row_idx]["id"]
                    supabase.table("processos").delete().eq("id", int(proc_id)).execute()
                needs_rerun = True

            if needs_rerun:
                st.toast("✅ Banco de dados atualizado com sucesso!")
                st.rerun()

    # --- Módulo de Inteligência Artificial ---
    st.divider()
    st.write("### 🧠 Módulo de Engenharia Jurídica (IA)")
    col_ai1, col_ai2 = st.columns([1, 2])

    with col_ai1:
        if not df.empty:
            processos_lista = df["numero"].tolist()
            processo_selecionado = st.selectbox("Selecione o processo alvo para redação:", processos_lista)
            
            if st.button("Gerar Petição Inicial Completa", type="primary", use_container_width=True):
                with st.spinner("Processando lógica jurídica e redigindo documento..."):
                    generate_piece(processo_selecionado)
        else:
            st.warning("Necessário cadastrar processos para utilizar o módulo de IA.")
