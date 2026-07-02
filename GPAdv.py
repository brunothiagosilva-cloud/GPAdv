# -*- coding: utf-8 -*-
# GPAdv_Web.py  —  "Meu Controle Jurídico" (Web / Supabase Enterprise)
# Versão Unificada: 36.8 (Métricas Dinâmicas, Filtros Inteligentes & Exportação Contextual)
# ------------------------------------------------------------------------------------

import os
import json
import re
import imaplib
import email
import datetime
import io
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from supabase import create_client, Client
from cryptography.fernet import Fernet

# --- Dependências Opcionais ---
try:
    import google.generativeai as genai
except ImportError:
    genai = None

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
    url = "https://cepzkxjdvtidonybkcte.supabase.co"
    key = "sb_publishable_287QhMSn5JrlsI8-0ut7uA_cfo-3MUe"
    return create_client(url, key)

supabase = init_connection()

# ---------------- Config & Paths Locais (E-mail/Cripto) ----------------
INSTALL_DIR = Path("C:/GerenciadorProcessos")
DATA_DIR = INSTALL_DIR / "data"

for p in (INSTALL_DIR, DATA_DIR):
    p.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = INSTALL_DIR / "config.json"
KEY_PATH = INSTALL_DIR / "secret.key"

DEFAULT_CONFIG = {
    "email": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "username": "",
        "password_enc": "",
        "folder": "INBOX"
    },
    "gemini": {
        "enabled": False,
        "api_key": ""
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
            if "gemini" not in cfg:
                cfg["gemini"] = {"enabled": False, "api_key": ""}
            if "email" not in cfg:
                cfg["email"] = {"imap_host": "imap.gmail.com", "imap_port": 993, "username": "", "password_enc": "", "folder": "INBOX"}
        return cfg
    except:
        return DEFAULT_CONFIG

CONFIG = load_config()

# --- Motor de IA com Auto-Discovery ---
def gerar_conteudo_ia(prompt):
    api_key = CONFIG.get("gemini", {}).get("api_key")
    if not genai or not api_key:
        raise ValueError("A API Key da Inteligência Artificial não foi configurada.")
        
    genai.configure(api_key=api_key)
    
    modelos_disponiveis = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            modelos_disponiveis.append(m.name)
            
    if not modelos_disponiveis:
        raise Exception("Sua chave de API não tem permissão para nenhum modelo de geração.")

    modelo_escolhido = None
    preferencias = ['models/gemini-1.5-flash', 'models/gemini-1.5-pro', 'models/gemini-1.0-pro', 'models/gemini-pro']
    
    for pref in preferencias:
        if pref in modelos_disponiveis:
            modelo_escolhido = pref
            break
            
    if not modelo_escolhido:
        modelo_escolhido = modelos_disponiveis[0]

    model = genai.GenerativeModel(modelo_escolhido)
    return model.generate_content(prompt)

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

def salvar_andamento(proc_num, desc):
    try:
        now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        supabase.table("historico_pecas").insert({
            "numero_processo": proc_num,
            "data_hora": now_str,
            "descricao": desc[:1500]
        }).execute()
    except Exception as e:
        st.error(f"Erro ao salvar andamento: {e}")

def get_historico(proc_num):
    try:
        res = supabase.table("historico_pecas").select("*").eq("numero_processo", proc_num).order("data_hora", desc=True).execute()
        return res.data
    except Exception:
        return []

def processar_pdf_movimentacao(uploaded_file):
    try:
        reader = PdfReader(uploaded_file)
        texto_pdf = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        
        prompt = f"""
        Extraia as seguintes informações deste texto de movimentação processual em um formato JSON puro (sem marcações markdown):
        - numero_processo (formato CNJ)
        - data_movimentacao (DD/MM/AAAA)
        - resumo_movimentacao (texto resumido do evento)
        - possui_prazo (true ou false)
        - prazo_final (data se houver, ou texto vazio)
        - nome_parte (nome completo da parte cliente, se identificado com clareza no documento. Se não encontrar, retorne vazio).
        
        Texto: {texto_pdf[:6000]}
        """
        
        resposta_ia = gerar_conteudo_ia(prompt)
        texto_resp = resposta_ia.text.replace('```json', '').replace('```', '').strip()
        dados_json = json.loads(texto_resp)
        
        num_processo = formatar_cnj(dados_json.get('numero_processo', ''))
        nome_extraido = dados_json.get('nome_parte', '')
        
        if num_processo:
            salvar_andamento(num_processo, f"Movimentação Extraída (PDF): {dados_json.get('resumo_movimentacao', '')}")
            
            if nome_extraido and len(nome_extraido.strip()) > 3:
                supabase.table("processos").update({"parte": nome_extraido.strip()}).eq("numero", num_processo).execute()
                st.session_state['temp_toast'] = f"Nome atualizado para: {nome_extraido.strip()}"
        
        return dados_json
    except Exception as e:
        st.error(f"Erro detalhado ao processar PDF via IA: {e}")
        return None

# ---------------- Gestão de Estado da Sessão (Autenticação) ----------------
if 'authenticated' not in st.session_state: st.session_state['authenticated'] = False
if 'user_email' not in st.session_state: st.session_state['user_email'] = ""
if 'perfil' not in st.session_state: st.session_state['perfil'] = ""
if 'recovery_email' not in st.session_state: st.session_state['recovery_email'] = None
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'temp_toast' not in st.session_state: st.session_state['temp_toast'] = ""
if 'pdf_json_result' not in st.session_state: st.session_state['pdf_json_result'] = None

def login(email_input, password_input):
    try:
        response = supabase.auth.sign_in_with_password({"email": email_input, "password": password_input})
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
    except Exception:
        st.error("Falha na autenticação: Verifique suas credenciais.")

def signup(email_input, password_input):
    try:
        supabase.auth.sign_up({"email": email_input, "password": password_input})
        st.success("🎉 Cadastro realizado com sucesso! Aguarde a liberação do seu e-mail no banco de dados para entrar.")
    except Exception as e:
        st.error(f"Falha ao realizar cadastro. (Erro: {e})")

def logout():
    try: supabase.auth.sign_out()
    except Exception: pass
    st.session_state.clear()
    st.rerun()

# ---------------- Funções de Banco de Dados (Supabase) ----------------
def load_data():
    try:
        response = supabase.table("processos").select("*").order("id", desc=True).execute()
        if not response.data:
            return pd.DataFrame(columns=["id", "numero", "tribunal", "parte", "situacao", "prazo", "observacoes", "marcado", "cor_card", "notif_data", "ultima_mov"])
            
        df_proc = pd.DataFrame(response.data)
        
        hist_res = supabase.table("historico_pecas").select("numero_processo, data_hora, descricao").order("data_hora", desc=True).execute()
        
        if hist_res.data:
            df_hist = pd.DataFrame(hist_res.data)
            df_hist_latest = df_hist.drop_duplicates(subset=['numero_processo'], keep='first').copy()
            df_hist_latest['ultima_mov'] = df_hist_latest['data_hora'].str[:10] + " - " + df_hist_latest['descricao'].str[:100] + "..."
            
            df_proc = pd.merge(df_proc, df_hist_latest[['numero_processo', 'ultima_mov']], left_on='numero', right_on='numero_processo', how='left')
            df_proc['ultima_mov'] = df_proc['ultima_mov'].fillna('Sem histórico')
            df_proc = df_proc.drop(columns=['numero_processo'])
        else:
            df_proc['ultima_mov'] = 'Sem histórico'
            
        return df_proc
    except Exception as e:
        st.error(f"Erro ao carregar dados do banco: {e}")
        return pd.DataFrame()

# ---------------- Funções de Importação e Exportação (Excel) ----------------
def export_to_excel(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_export = df.drop(columns=['id', 'ultima_mov'], errors='ignore')
        df_export.to_excel(writer, index=False, sheet_name='Processos_GPAdv')
        workbook = writer.book
        worksheet = writer.sheets['Processos_GPAdv']
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
        for col_num, value in enumerate(df_export.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 20)
    return output.getvalue()

def import_from_excel(uploaded_file):
    try:
        df_import = pd.read_excel(uploaded_file, engine='openpyxl')
        df_import.columns = [str(c).lower().strip() for c in df_import.columns]
        
        if "numero" not in df_import.columns and "número" not in df_import.columns and "cnj" not in df_import.columns:
            st.error("A planilha precisa ter pelo menos uma coluna chamada 'numero' ou 'cnj' para identificar os processos.")
            return False
            
        df_import = df_import.fillna("")
        registros = df_import.to_dict(orient="records")
        registros_limpos = []
        
        for reg in registros:
            num_raw = reg.get("numero", reg.get("número", reg.get("cnj", "")))
            if not num_raw: 
                continue 
                
            num_formatado = formatar_cnj(str(num_raw))
            registros_limpos.append({
                "numero": num_formatado,
                "tribunal": str(reg.get("tribunal", "TJ-SP")),
                "parte": str(reg.get("parte", str(reg.get("cliente", "")))),
                "situacao": str(reg.get("situacao", str(reg.get("status", "Em Andamento")))),
                "prazo": str(reg.get("prazo", "")),
                "observacoes": str(reg.get("observacoes", str(reg.get("obs", "")))),
                "marcado": "",
                "cor_card": "",
                "notif_data": ""
            })
            
        if registros_limpos:
            supabase.table("processos").insert(registros_limpos).execute()
            return True
        else:
            st.warning("Não foram encontrados números válidos para importação.")
            return False
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

    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        M.login(imap_user, imap_pwd)
        M.select('"INBOX"')
        
        typ, data = M.search(None, '(UNSEEN)')
        if typ != 'OK' or not data[0]:
            st.info("Nenhuma mensagem não lida encontrada.")
            M.logout()
            return

        regex = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
        processos_atualizados = 0
        
        for num in data[0].split():
            typ, msg_data = M.fetch(num, '(RFC822)')
            msg = email.message_from_bytes(msg_data[0][1])
            
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", "ignore")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", "ignore")
                
            proc_nums = regex.findall(body)
            
            if proc_nums:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d")
                for proc_num in set(proc_nums):
                    supabase.table("processos").update(
                        {"marcado": "📩", "notif_data": now_str}
                    ).eq("numero", proc_num).execute()
                    
                    resumo = f"Publicação/Notificação via E-mail:\n{body[:800]}..."
                    salvar_andamento(proc_num, resumo)
                    processos_atualizados += 1
        
        M.logout()
        if processos_atualizados > 0:
            st.success(f"{processos_atualizados} andamentos vinculados automaticamente via e-mail!")
        else:
            st.info("Nenhum número de processo correspondente encontrado nos e-mails.")
            
    except Exception as e:
        st.error(f"Falha de comunicação IMAP: {e}")

def generate_piece(proc_num):
    try:
        prompt = f"Aja como um advogado sênior. Elabore uma Petição Inicial completa e estruturada para o processo {proc_num}. Não inclua resumos, gere a peça em sua totalidade abordando fatos, direito e pedidos de forma genérica para preenchimento posterior."
        resposta = gerar_conteudo_ia(prompt)
        texto = resposta.text
        
        salvar_andamento(proc_num, "Petição Inicial (Gerada via IA - Gemini)")
                         
        st.success(f"Peça processual gerada para {proc_num} com sucesso!")
        with st.expander("Visualizar Documento Gerado", expanded=True):
            st.write(texto)
            st.download_button("Baixar Texto (TXT)", data=texto, file_name=f"Petição_{proc_num}.txt", mime="text/plain")
            
    except Exception as e:
        st.error(f"Erro na API do Gemini: {e}")


# ==============================================================================
# RENDERIZAÇÃO DA INTERFACE PRINCIPAL
# ==============================================================================

if st.session_state['temp_toast']:
    st.toast(st.session_state['temp_toast'], icon="✅")
    st.session_state['temp_toast'] = ""

if not st.session_state.get('authenticated'):
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
                        with st.spinner("Autenticando..."): login(auth_email, auth_senha)
                    else:
                        st.warning("Preencha todos os campos para entrar.")
                        
            with tab2:
                new_email = st.text_input("Novo e-mail", key="reg_email")
                new_senha = st.text_input("Crie uma senha (mínimo 6 caracteres)", type="password", key="reg_pwd")
                if st.button("Criar Conta", use_container_width=True):
                    if new_email and len(new_senha) >= 6:
                        with st.spinner("Registrando..."): signup(new_email, new_senha)
                    else:
                        st.warning("Preencha um e-mail válido e senha maior que 6 caracteres.")
                        
            with tab3:
                rec_email = st.text_input("E-mail corporativo", key="rec_email")
                if st.button("Enviar Código", use_container_width=True):
                    if rec_email:
                        try:
                            supabase.auth.reset_password_for_email(rec_email)
                            st.session_state['recovery_email'] = rec_email 
                            st.success("E-mail enviado! Verifique o código numérico.")
                        except Exception as e:
                            st.error(f"Erro: {e}")
                    else:
                        st.warning("Insira o seu e-mail.")
                
                if st.session_state.get('recovery_email'):
                    st.divider()
                    otp_code = st.text_input("Código de 6 dígitos", key="otp_code")
                    new_pwd = st.text_input("Nova Senha", type="password", key="rec_new_pwd")
                    if st.button("Confirmar Nova Senha", type="primary", use_container_width=True):
                        if otp_code and len(new_pwd) >= 6:
                            try:
                                supabase.auth.verify_otp({"email": st.session_state['recovery_email'], "token": otp_code, "type": "recovery"})
                                supabase.auth.update_user({"password": new_pwd})
                                supabase.auth.sign_out()
                                st.session_state['recovery_email'] = None
                                st.success("Senha redefinida com sucesso!")
                            except Exception:
                                st.error("Código inválido/expirado.")
else:
    # --- Sidebar ---
    with st.sidebar:
        st.markdown(f"👤 **Usuário:** {st.session_state['user_email']}")
        st.markdown(f"🛡️ **Perfil:** {str(st.session_state.get('perfil', 'usuario')).capitalize()}")
        if st.button("🚪 Sair do Sistema", use_container_width=True):
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
                    "numero": num_formatado, "tribunal": "TJ-SP", "parte": new_parte, 
                    "situacao": "Em Andamento", "prazo": "", "observacoes": "", 
                    "marcado": "", "cor_card": "", "notif_data": ""
                }).execute()
                st.success(f"Processo {num_formatado} cadastrado!")
                st.rerun()

        st.divider()
        
        if st.button("📧 Sincronizar Publicações (IMAP)", use_container_width=True):
            with st.spinner("Varrendo caixa de entrada..."):
                read_publications_from_email()
                st.rerun()

        st.divider()

        st.markdown("### 🤖 Assistente Gemini")
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
                
        if prompt := st.chat_input("Pergunte sobre os processos..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)
            
            df_context = load_data()
            contexto_txt = df_context.to_string(index=False) if not df_context.empty else "Nenhum processo."
            
            with st.chat_message("assistant"):
                with st.spinner("Consultando dados..."):
                    try:
                        prompt_completo = f"Você é o assistente jurídico GPAdv. Responda baseando-se NESTA tabela:\n{contexto_txt}\n\nPergunta: {prompt}"
                        resposta = gerar_conteudo_ia(prompt_completo)
                        st.markdown(resposta.text)
                        st.session_state.messages.append({"role": "assistant", "content": resposta.text})
                    except Exception as e:
                        st.error(f"Erro na IA: {e}")

    # --- Área Principal ---
    col_t1, col_t2, col_t3 = st.columns([6, 1.5, 2.5], vertical_alignment="bottom")
    
    with col_t1:
        st.title("⚖️ GPAdv")
        st.caption("Versão Corporativa 36.8 (Métricas Dinâmicas, Filtros Inteligentes & Exportação Contextual)")
        
    with col_t2:
        if st.button("🔄 Atualizar", use_container_width=True):
            st.rerun()
            
    with col_t3:
        with st.popover("📜 Histórico de Versão", use_container_width=True):
            st.markdown("""
            **Resumo de Funcionalidades (v36.8)**
            * **Métricas Dinâmicas (NOVO):** Os painéis numéricos agora reagem em tempo real aos filtros aplicados na tela.
            * **Exportação Contextual (NOVO):** O botão Excel agora exporta apenas os dados que estiverem filtrados no Dashboard.
            * **Edição de Grid (Correção Técnica):** Salvamento simultâneo e seguro de status e textos diretamente na tabela.
            * **Filtro de Abas/Status:** Navegue rapidamente por status.
            * **Auto-Refresh Inteligente:** Recarregamento automatizado de tela após ações de IA ou extrações IMAP.
            * **Motor IA (Auto-Discovery):** Fim dos erros 404, seleção fluída de modelos de Inteligência Artificial.
            """)

    st.write("")
    tab_dash, tab_config, tab_ia = st.tabs(["📊 Dashboard Central", "⚙️ Configurações Pessoais", "🤖 Inteligência de Documentos (PDF)"])

    # ---------------- TAB 1: DASHBOARD CENTRAL ----------------
    with tab_dash:
        df = load_data()
        
        # 1. Aloca os espaços na tela (Placeholders)
        metrics_placeholder = st.empty()
        
        with st.container(border=True):
            col_search, col_export, col_import = st.columns([2, 1, 1], vertical_alignment="bottom")
            with col_search:
                busca = st.text_input("🔍 Buscar em qualquer campo:", placeholder="Digite número, parte, tribunal...")
            
            # Espaço para o botão de exportação que será renderizado depois dos filtros
            export_placeholder = col_export.empty()
            
            with col_import:
                with st.popover("📤 Importar Excel", use_container_width=True):
                    st.write("A planilha deve conter uma coluna 'numero' ou 'CNJ'.")
                    uploaded_file = st.file_uploader("", type=["xlsx"])
                    if uploaded_file is not None:
                        if st.button("Confirmar Importação", type="primary"):
                            with st.spinner("Processando..."):
                                if import_from_excel(uploaded_file):
                                    st.success("Importação concluída!")
                                    time.sleep(1)
                                    st.rerun()

        st.write("### Base de Dados")
        
        filtro_status = st.radio(
            "Filtrar por Status:",
            options=["Todos", "Em Andamento", "Arquivado", "Suspenso", "Concluído"],
            horizontal=True,
            label_visibility="collapsed"
        )

        # 2. Aplica a Lógica de Negócio dos Filtros
        df_display = df.copy()
        
        if busca and not df_display.empty:
            df_display = df_display[df_display.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)]
            
        if filtro_status != "Todos" and not df_display.empty:
            df_display = df_display[df_display['situacao'].str.contains(filtro_status, case=False, na=False)]

        # 3. Desenha as Métricas (agora alimentadas pelo df_display filtrado)
        with metrics_placeholder.container():
            if not df_display.empty:
                met1, met2, met3 = st.columns(3)
                titulo_total = "Total de Processos" if filtro_status == "Todos" else f"Total ({filtro_status})"
                met1.metric(titulo_total, len(df_display))
                met2.metric("Com Publicação Recente", len(df_display[df_display['marcado'] == '📩']))
                met3.metric("Em Andamento", len(df_display[df_display['situacao'].str.contains('Andamento', case=False, na=False)]))
            else:
                met1, met2, met3 = st.columns(3)
                met1.metric("Total de Processos", 0)
                met2.metric("Com Publicação Recente", 0)
                met3.metric("Em Andamento", 0)

        # 4. Desenha o botão de Exportação contextual
        with export_placeholder:
            if not df_display.empty:
                excel_bytes = export_to_excel(df_display)
                st.download_button(label="📥 Exportar Excel", data=excel_bytes, file_name=f"Relatorio_GPAdv.xlsx", use_container_width=True)
            else:
                st.download_button(label="📥 Exportar Excel", data=b"", file_name="vazio.xlsx", disabled=True, use_container_width=True)

        # 5. Desenha o Grid
        st.caption("Edição Inline: Dê um duplo clique para editar. Nova coluna 'Último Histórico' exibe a movimentação mais recente.")

        if df_display.empty:
            st.info(f"Nenhum processo listado sob o filtro atual.")
        else:
            edited_df = st.data_editor(
                df_display,
                use_container_width=True,
                num_rows="dynamic",
                hide_index=True,
                column_config={
                    "id": None, "cor_card": None, "notif_data": None,
                    "numero": st.column_config.TextColumn("Número (CNJ)", required=True),
                    "situacao": st.column_config.SelectboxColumn("Status", options=["Em Andamento", "Arquivado", "Suspenso", "Concluído"]),
                    "ultima_mov": st.column_config.TextColumn("Último Histórico", disabled=True, width="large"),
                },
                key="process_editor"
            )

            if st.session_state.get("process_editor"):
                changes = st.session_state["process_editor"]
                needs_rerun = False
                
                if changes.get("edited_rows"):
                    for row_idx_str, col_changes in changes["edited_rows"].items():
                        proc_id = df_display.iloc[int(row_idx_str)]["id"]
                        supabase.table("processos").update(col_changes).eq("id", int(proc_id)).execute()
                    needs_rerun = True
                    
                if changes.get("deleted_rows"):
                    for row_idx in changes["deleted_rows"]:
                        proc_id = df_display.iloc[int(row_idx)]["id"]
                        supabase.table("processos").delete().eq("id", int(proc_id)).execute()
                    needs_rerun = True

                if needs_rerun:
                    st.toast("✅ Banco de dados atualizado com sucesso!")
                    del st.session_state["process_editor"]
                    st.rerun()

        st.divider()
        
        st.write("### 📜 Linha do Tempo & Engenharia Jurídica (IA)")
        col_hist, col_ai = st.columns([2, 1])

        with col_hist:
            proc_list = df["numero"].tolist() if not df.empty else []
            proc_escolhido = st.selectbox("Selecione um processo para visualizar o histórico completo:", proc_list)
            
            if proc_escolhido:
                hist_dados = get_historico(proc_escolhido)
                if hist_dados:
                    for item in hist_dados:
                        with st.expander(f"🗓️ {item.get('data_hora', '')} - Visualizar Teor"):
                            st.write(item.get('descricao', ''))
                else:
                    st.info("Ainda não há movimentações registradas (via E-mail ou PDF) para este processo.")

        with col_ai:
            st.write("<br>", unsafe_allow_html=True)
            if proc_escolhido:
                if st.button("Gerar Petição Inicial (IA)", type="primary", use_container_width=True):
                    with st.spinner("Processando lógica jurídica..."):
                        generate_piece(proc_escolhido)
            else:
                st.warning("Selecione um processo ao lado para redigir a peça.")

    # ---------------- TAB 2: CONFIGURAÇÕES PESSOAIS ----------------
    with tab_config:
        col_conf1, col_conf2 = st.columns(2)
        
        with col_conf1:
            st.subheader("⚙️ Integração de E-mail (IMAP)")
            st.write("Configuração para o auto-save de publicações.")
            with st.container(border=True):
                email_imap = st.text_input("Seu E-mail Profissional (Ex: seuemail@gmail.com)")
                pwd_imap = st.text_input("Senha de Aplicativo (App Password)", type="password")
                
                if st.button("Salvar E-mail IMAP", type="primary"):
                    if email_imap and pwd_imap:
                        supabase.table("configuracoes").upsert({
                            "usuario_email": st.session_state['user_email'],
                            "imap_email": email_imap,
                            "imap_pwd": pwd_imap
                        }).execute()
                        st.success("Configurações IMAP vinculadas ao seu perfil!")
                    else:
                        st.warning("Preencha o e-mail e a senha para salvar.")

        with col_conf2:
            st.subheader("🤖 Configuração Gemini (Google AI Studio)")
            st.write("Habilita o Chat Inteligente e a Leitura de PDF.")
            with st.container(border=True):
                gemini_key = st.text_input("Sua Chave API (AIzaSy...)", value=CONFIG.get("gemini", {}).get("api_key", ""), type="password")
                
                if st.button("Habilitar Gemini AI", type="primary", use_container_width=True):
                    if "gemini" not in CONFIG:
                        CONFIG["gemini"] = {}
                    CONFIG["gemini"]["api_key"] = gemini_key
                    CONFIG["gemini"]["enabled"] = True if gemini_key else False
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(CONFIG, f, ensure_ascii=False, indent=2)
                    st.success("Configuração de IA salva localmente! O assistente já está pronto.")

    # ---------------- TAB 3: INTELIGÊNCIA DE PDF ----------------
    with tab_ia:
        st.subheader("🤖 Extrator de Movimentações (PDF)")
        st.write("Faça o upload do documento. O Gemini lerá o PDF, identificará o processo, atualizará o nome da parte (se incompleto), resumirá o teor e salvará automaticamente na Linha do Tempo.")
        
        uploaded_pdf = st.file_uploader("Selecione o arquivo PDF", type=["pdf"])
        
        if uploaded_pdf is not None:
            if st.session_state['pdf_json_result']:
                st.success("✅ Dados extraídos e salvos no histórico com sucesso!")
                st.json(st.session_state['pdf_json_result'])
                
                if st.button("Limpar e Processar Novo", type="secondary"):
                    st.session_state['pdf_json_result'] = None
                    st.rerun()
            else:
                if st.button("Processar Movimentação via IA", type="primary", use_container_width=True):
                    with st.spinner("Consultando servidores do Google e extraindo dados..."):
                        resultado = processar_pdf_movimentacao(uploaded_pdf)
                        if resultado:
                            st.session_state['pdf_json_result'] = resultado
                            st.rerun()
