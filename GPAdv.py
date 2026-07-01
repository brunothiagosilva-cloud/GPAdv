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

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

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
    # Substitua pelas suas chaves reais do projeto
    url = "https://cepzkxjdvtidonybkcte.supabase.co"
    key = "sb_publishable_287QhMSn5JrlsI8-0ut7uA_cfo-3MUe"
    return create_client(url, key)

supabase = init_connection()

# ---------------- Config & Paths Locais (E-mail/Cripto) ----------------
APP_VERSION = "32.0 (Enterprise Auth, AI & OTP Recovery)"
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

# ---------------- Lógica de Negócios (CNJ, Permissões, PDF) ----------------
def formatar_cnj(numero):
    n = re.sub(r'\D', '', numero)
    if len(n) == 20:
        return f"{n[0:7]}-{n[7:9]}.{n[9:13]}.{n[13]}.{n[14:16]}.{n[16:20]}"
    return numero

def verificar_acesso(email_usuario):
    try:
        res = supabase.table("permissoes").select("perfil").eq("email", email_usuario).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception:
        return None

def processar_pdf_movimentacao(uploaded_file):
    if not openai or not CONFIG.get("openai", {}).get("enabled"):
        st.warning("Módulo OpenAI não está habilitado ou configurado.")
        return None
        
    try:
        reader = PdfReader(uploaded_file)
        texto_pdf = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        
        openai.api_key = CONFIG["openai"]["api_key"] or OPENAI_API_KEY
        prompt = f"""
        Extraia as seguintes informações deste texto de movimentação processual em formato JSON:
        - numero_processo (formato CNJ)
        - data_movimentacao (DD/MM/AAAA)
        - resumo_movimentacao (texto resumido do evento)
        - possui_prazo (true ou false)
        - prazo_final (data se houver, ou texto vazio)
        
        Texto: {texto_pdf[:4000]}
        """
        
        resp = openai.ChatCompletion.create(
            model=CONFIG["openai"]["model"],
            messages=[{"role": "user", "content": prompt}]
        )
        
        texto_resp = resp['choices'][0]['message']['content']
        texto_resp = texto_resp.replace('```json', '').replace('```', '').strip()
        dados_json = json.loads(texto_resp)
        
        supabase.table("historico_pecas").insert({
            "numero_processo": dados_json.get('numero_processo', ''),
            "data_hora": dados_json.get('data_movimentacao', ''),
            "descricao": f"Movimentação Extraída (PDF): {dados_json.get('resumo_movimentacao', '')}"
        }).execute()
        
        return dados_json
    except Exception as e:
        st.error(f"Erro ao processar PDF via IA: {e}")
        return None

# ---------------- Gestão de Estado da Sessão (Autenticação) ----------------
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user_email' not in st.session_state:
    st.session_state['user_email'] = ""
if 'perfil' not in st.session_state:
    st.session_state['perfil'] = ""
if 'recovery_email' not in st.session_state:
    st.session_state['recovery_email'] = None

def login(email_input, password_input):
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email_input,
            "password": password_input
        })
        if response.user:
            acesso = verificar_acesso(response.user.email)
            if acesso:
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = response.user.email
                st.session_state['perfil'] = acesso.get('perfil', 'usuario')
                st.success("Login realizado com sucesso!")
                st.rerun()
            else:
                st.error("Acesso não autorizado para este e-mail. Solicite liberação ao administrador.")
                supabase.auth.sign_out()
    except Exception as e:
        st.error(f"Falha na autenticação: Verifique suas credenciais.")

def signup(email_input, password_input):
    try:
        response = supabase.auth.sign_up({
            "email": email_input,
            "password": password_input
        })
        st.success("🎉 Cadastro realizado com sucesso! Aguarde a liberação do seu e-mail no banco de dados para entrar.")
    except Exception as e:
        st.error(f"Falha ao realizar cadastro. (Erro: {e})")

def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state['authenticated'] = False
    st.session_state['user_email'] = ""
    st.session_state['perfil'] = ""
    st.session_state['recovery_email'] = None
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
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Processos_GPAdv')
        workbook = writer.book
        worksheet = writer.sheets['Processos_GPAdv']
        
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 20)
            
    return output.getvalue()

def import_from_excel(uploaded_file):
    try:
        df_import = pd.read_excel(uploaded_file, engine='openpyxl')
        
        colunas_esperadas = ["numero", "tribunal", "parte", "situacao", "prazo"]
        for col in colunas_esperadas:
            if col not in df_import.columns:
                st.error(f"A coluna '{col}' está ausente na planilha. O formato está incorreto.")
                return False
                
        df_import = df_import.fillna("")
        registros = df_import.to_dict(orient="records")
        
        registros_limpos = []
        for reg in registros:
            num_formatado = formatar_cnj(str(reg.get("numero", "")))
            registros_limpos.append({
                "numero": num_formatado,
                "tribunal": str(reg.get("tribunal", "TJ-SP")),
                "parte": str(reg.get("parte", "")),
                "situacao": str(reg.get("situacao", "Em Andamento")),
                "prazo": str(reg.get("prazo", "")),
                "observacoes": str(reg.get("observacoes", "")),
                "marcado": str(reg.get("marcado", "")),
                "cor_card": str(reg.get("cor_card", "")),
                "notif_data": str(reg.get("notif_data", ""))
            })
            
        if registros_limpos:
            supabase.table("processos").insert(registros_limpos).execute()
            return True
            
    except Exception as e:
        st.error(f"Erro ao processar o arquivo Excel: {e}")
        return False

# ---------------- Lógica de Negócios (Email & IA) ----------------
def read_publications_from_email():
    try:
        res = supabase.table("configuracoes").select("*").eq("usuario_email", st.session_state['user_email']).execute()
        if res.data and res.data[0].get("imap_email") and res.data[0].get("imap_pwd"):
            imap_user = res.data[0]["imap_email"]
            imap_pwd = res.data[0]["imap_pwd"]
        else:
            st.warning("Configure suas credenciais de e-mail na aba '⚙️ Configurações Pessoais' antes de sincronizar publicações.")
            return
    except Exception as e:
        st.error(f"Erro ao buscar configurações de e-mail: {e}")
        return

    imap_host = "imap.gmail.com"
    imap_port = 993

    try:
        M = imaplib.IMAP4_SSL(imap_host, imap_port)
        M.login(imap_user, imap_pwd)
        M.select('"INBOX"')
        
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
    # TELA DE LOGIN / CADASTRO / RECUPERAÇÃO
    st.markdown("<h1 style='text-align: center; margin-top: 5vh;'>⚖️ GPAdv</h1>", unsafe_allow_html=True)
    st.markdown("<h4 style='text-align: center; color: gray;'>Sistema Corporativo de Gestão Jurídica</h4>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.container(border=True):
            tab1, tab2, tab3 = st.tabs(["🔐 Entrar", "📝 Cadastre-se", "🔑 Esqueci a Senha"])
            
            with tab1:
                auth_email = st.text_input("E-mail corporativo", key="log_email")
                auth_senha = st.text_input("Senha", type="password", key="log_pwd")
                
                if st.button("Acessar", type="primary", use_container_width=True):
                    if auth_email and auth_senha:
                        with st.spinner("Autenticando e verificando permissões..."):
                            login(auth_email, auth_senha)
                    else:
                        st.warning("Preencha todos os campos para entrar.")
                        
            with tab2:
                st.markdown("Crie sua conta para acessar a plataforma.")
                new_email = st.text_input("Novo e-mail", key="reg_email")
                new_senha = st.text_input("Crie uma senha (mínimo 6 caracteres)", type="password", key="reg_pwd")
                
                if st.button("Criar Conta", use_container_width=True):
                    if new_email and len(new_senha) >= 6:
                        with st.spinner("Registrando..."):
                            signup(new_email, new_senha)
                    else:
                        st.warning("Preencha um e-mail válido e uma senha com no mínimo 6 caracteres.")
                        
            with tab3:
                st.markdown("Insira seu e-mail para receber um código numérico de recuperação.")
                rec_email = st.text_input("E-mail corporativo", key="rec_email")
                
                if st.button("Enviar Código de Recuperação", use_container_width=True):
                    if rec_email:
                        with st.spinner("Solicitando código seguro..."):
                            try:
                                supabase.auth.reset_password_for_email(rec_email)
                                st.session_state['recovery_email'] = rec_email 
                                st.success("E-mail enviado! Verifique o código numérico na sua caixa de entrada (ou spam).")
                            except Exception as e:
                                st.error(f"Erro ao solicitar recuperação.")
                    else:
                        st.warning("Por favor, insira o seu e-mail de acesso.")
                
                if st.session_state.get('recovery_email'):
                    st.divider()
                    st.markdown("### 2. Criar Nova Senha")
                    otp_code = st.text_input("Código de 6 dígitos recebido no e-mail", key="otp_code")
                    new_pwd = st.text_input("Crie sua Nova Senha (mínimo 6 caracteres)", type="password", key="rec_new_pwd")
                    
                    if st.button("Confirmar Nova Senha", type="primary", use_container_width=True):
                        if otp_code and len(new_pwd) >= 6:
                            try:
                                supabase.auth.verify_otp({
                                    "email": st.session_state['recovery_email'], 
                                    "token": otp_code, 
                                    "type": "recovery"
                                })
                                supabase.auth.update_user({"password": new_pwd})
                                supabase.auth.sign_out()
                                st.session_state['recovery_email'] = None
                                st.success("✅ Senha redefinida com sucesso! Volte para a aba 'Entrar'.")
                            except Exception as e:
                                st.error(f"Código inválido/expirado ou erro interno.")
                        else:
                            st.warning("Preencha o código e certifique-se de que a nova senha tem no mínimo 6 caracteres.")
else:
    # TELA DO SISTEMA AUTENTICADO
    
    # --- Sidebar ---
    with st.sidebar:
        st.markdown(f"👤 **Usuário:** {st.session_state['user_email']}")
        st.markdown(f"🛡️ **Perfil:** {str(st.session_state.get('perfil', 'usuario')).capitalize()}")
        if st.button("Sair do Sistema", use_container_width=True):
            logout()
            
        st.divider()
        st.header("Ações Operacionais")
        
        with st.form("add_process_form", clear_on_submit=True):
            st.subheader("➕ Novo Processo")
            new_numero = st.text_input("Número do Processo (Mascara CNJ Automática)")
            new_parte = st.text_input("Nome da Parte")
            submitted = st.form_submit_button("Salvar Registro", use_container_width=True)
            
            if submitted and new_numero:
                num_formatado = formatar_cnj(new_numero)
                supabase.table("processos").insert({
                    "numero": num_formatado, 
                    "tribunal": "TJ-SP", 
                    "parte": new_parte, 
                    "situacao": "Em Andamento", 
                    "prazo": "", 
                    "observacoes": "", 
                    "marcado": "", 
                    "cor_card": "", 
                    "notif_data": ""
                }).execute()
                st.success(f"Processo {num_formatado} cadastrado!")
                st.rerun()

        st.divider()
        
        if st.button("📧 Sincronizar Publicações (IMAP)", use_container_width=True):
            with st.spinner("Varrendo caixa de entrada..."):
                read_publications_from_email()
                st.rerun()

    # --- Área Principal (Tabs Superiores) ---
    st.title(f"⚖️ GPAdv")
    st.caption(f"Versão Corporativa {APP_VERSION}")

    tab_dash, tab_config, tab_ia = st.tabs(["📊 Dashboard Central", "⚙️ Configurações Pessoais", "🤖 Inteligência de Documentos (PDF)"])

    # ---------------- TAB 1: DASHBOARD CENTRAL ----------------
    with tab_dash:
        df = load_data()

        if not df.empty:
            met1, met2, met3 = st.columns(3)
            met1.metric("Total de Processos", len(df))
            met2.metric("Com Publicação Recente", len(df[df['marcado'] == '📩']))
            met3.metric("Em Andamento", len(df[df['situacao'].str.contains('Andamento', case=False, na=False)]))

        with st.container(border=True):
            col_search, col_export, col_import = st.columns([2, 1, 1])
            
            with col_search:
                busca = st.text_input("🔍 Buscar em qualquer campo:", placeholder="Digite número, parte, tribunal...")
                if busca and not df.empty:
                    df = df[df.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)]
                    
            with col_export:
                st.write("<br>", unsafe_allow_html=True)
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
                            with st.spinner("Formatando CNJs e inserindo registros..."):
                                sucesso = import_from_excel(uploaded_file)
                                if sucesso:
                                    st.success("Importação concluída!")
                                    st.rerun()

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
                    "id": None,
                    "cor_card": None,
                    "notif_data": None,
                    "numero": st.column_config.TextColumn("Número (CNJ)", required=True),
                    "situacao": st.column_config.SelectboxColumn("Status", options=["Em Andamento", "Arquivado", "Suspenso", "Concluído"]),
                },
                key="process_editor"
            )

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

    # ---------------- TAB 2: CONFIGURAÇÕES PESSOAIS ----------------
    with tab_config:
        col_conf1, col_conf2 = st.columns(2)
        
        with col_conf1:
            st.subheader("⚙️ Integração de E-mail (IMAP)")
            st.write("Estas credenciais são exclusivas para o seu usuário e serão salvas de forma segura no banco de dados.")
            with st.container(border=True):
                email_imap = st.text_input("Seu E-mail Profissional (Ex: seuemail@gmail.com)")
                pwd_imap = st.text_input("Senha de Aplicativo (App Password)", type="password")
                
                if st.button("Salvar Minhas Configurações", type="primary"):
                    if email_imap and pwd_imap:
                        supabase.table("configuracoes").upsert({
                            "usuario_email": st.session_state['user_email'],
                            "imap_email": email_imap,
                            "imap_pwd": pwd_imap
                        }).execute()
                        st.success("Configurações IMAP vinculadas ao seu perfil com sucesso!")
                    else:
                        st.warning("Preencha o e-mail e a senha para salvar.")

        with col_conf2:
            st.subheader("🔑 Alterar Minha Senha")
            st.write("Caso você deseje trocar sua credencial de acesso atual de forma manual.")
            with st.container(border=True):
                nova_senha_update = st.text_input("Nova Senha de Acesso", type="password", key="new_pwd_update")
                if st.button("Atualizar Senha", type="primary", use_container_width=True):
                    if len(nova_senha_update) >= 6:
                        try:
                            supabase.auth.update_user({"password": nova_senha_update})
                            st.success("Senha atualizada no banco de dados com sucesso!")
                        except Exception as e:
                            st.error(f"Erro ao atualizar senha: {e}")
                    else:
                        st.warning("A senha deve ter no mínimo 6 caracteres.")

    # ---------------- TAB 3: INTELIGÊNCIA DE PDF ----------------
    with tab_ia:
        st.subheader("🤖 Extrator de Movimentações (PDF)")
        st.write("Faça o upload do documento de movimentação do tribunal. A inteligência artificial lerá o PDF, identificará o número do processo, prazos e registrará o histórico automaticamente no seu banco de dados.")
        
        uploaded_pdf = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
        
        if uploaded_pdf is not None:
            if st.button("Processar Movimentação via IA", type="primary", use_container_width=True):
                with st.spinner("Lendo documento e extraindo dados críticos..."):
                    resultado = processar_pdf_movimentacao(uploaded_pdf)
                    if resultado:
                        st.success("✅ Dados extraídos e salvos no histórico com sucesso!")
                        st.json(resultado)
