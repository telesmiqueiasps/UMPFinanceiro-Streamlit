import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import extract
from models import db, Usuario, Configuracao, Lancamento, SaldoFinal
import os
from datetime import datetime
import locale
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from collections import defaultdict
from fpdf import FPDF
from PIL import Image
import traceback
import random
import re
import requests
from dotenv import load_dotenv

# Configuração de localização para formatar moeda
def format_currency_brl(value, include_symbol=True):
    formatted = f"{value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"R$ {formatted}" if include_symbol else formatted

load_dotenv()

# Configuração do banco
DATABASE_URI = f"sqlite:///{os.path.abspath('instance/database.db')}?timeout=10"
engine = create_engine(DATABASE_URI)
db.Model.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

# Configuração de uploads e relatórios
UPLOAD_FOLDER = 'uploads/'
RELATORIOS_DIR = 'relatorios/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}
for folder in [UPLOAD_FOLDER, RELATORIOS_DIR]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Estado da sessão
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'current_user' not in st.session_state:
    st.session_state['current_user'] = None
if 'user_id' not in st.session_state:
    st.session_state['user_id'] = None
if 'edit_lancamento_id' not in st.session_state:
    st.session_state['edit_lancamento_id'] = None
if 'recuperar_senha' not in st.session_state:
    st.session_state['recuperar_senha'] = False    

# Funções auxiliares
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def verificar_email_existente(email, id_usuario):
    config_existente = session.query(Configuracao).filter_by(email=email).first()
    if config_existente and config_existente.id_usuario != id_usuario:
        return True
    return False

def carregar_administradores():
    administradores = defaultdict(list)
    registros = session.query(Configuracao).all()
    for registro in registros:
        if registro.admin:
            administradores[registro.admin].append((registro.id_usuario, registro.ump_federacao))
    return {admin: usuarios for admin, usuarios in administradores.items()}

def get_usuarios_autorizados():
    administradores = carregar_administradores()
    return administradores.get(st.session_state['user_id'], [])

def obter_saldo_inicial(mes, ano):
    ano_vigente = session.query(Configuracao.ano_vigente).filter_by(id_usuario=st.session_state['user_id']).scalar()
    if not ano_vigente:
        return 0
    if ano != ano_vigente:
        ano = ano_vigente

    if mes == 1:
        saldo_inicial = session.query(Configuracao.saldo_inicial).filter_by(id_usuario=st.session_state['user_id']).scalar()
        return saldo_inicial if saldo_inicial is not None else 0

    saldo_anterior = session.query(SaldoFinal.saldo).filter_by(
        mes=mes - 1,
        ano=ano,
        id_usuario=st.session_state['user_id']
    ).scalar()
    return saldo_anterior if saldo_anterior is not None else 0

def calcular_saldo_final(mes, ano, saldo_inicial):
    entradas = session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Receitas') | (Lancamento.tipo == 'ACI Recebida'),
        extract('month', Lancamento.data) == mes,
        extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0

    saidas = session.query(db.func.sum(Lancamento.valor)).filter(
        (Lancamento.tipo == 'Outras Despesas') | (Lancamento.tipo == 'ACI Enviada'),
        extract('month', Lancamento.data) == mes,
        extract('year', Lancamento.data) == ano,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0

    saldo_final = saldo_inicial + entradas - saidas
    return saldo_final

def salvar_saldo_final(mes, ano, saldo_inicial):
    saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)
    saldo_existente = session.query(SaldoFinal).filter(
        SaldoFinal.mes == mes,
        SaldoFinal.ano == ano,
        SaldoFinal.id_usuario == st.session_state['user_id']
    ).first()

    if saldo_existente:
        saldo_existente.saldo = saldo_final
    else:
        saldo_novo = SaldoFinal(
            mes=mes,
            ano=ano,
            saldo=saldo_final,
            id_usuario=st.session_state['user_id']
        )
        session.add(saldo_novo)
    session.commit()

def atualizar_saldos_iniciais():
    saldo_inicial = session.query(Configuracao.saldo_inicial).filter_by(id_usuario=st.session_state['user_id']).first()
    if saldo_inicial:
        saldo_inicial = saldo_inicial[0]
    else:
        saldo_inicial = 0

    for mes in range(1, 13):
        saldo_existente = session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.id_usuario == st.session_state['user_id']
        ).first()
        
        if saldo_existente:
            saldo_existente.saldo = saldo_inicial
        else:
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=2025,
                saldo=saldo_inicial,
                id_usuario=st.session_state['user_id']
            )
            session.add(saldo_novo)
    session.commit()

def recalcular_saldos_finais():
    meses_anos = session.query(SaldoFinal.mes, SaldoFinal.ano).filter(
        SaldoFinal.id_usuario == st.session_state['user_id']
    ).distinct().all()

    for mes, ano in meses_anos:
        saldo_inicial = obter_saldo_inicial(mes, ano)
        saldo_final = calcular_saldo_final(mes, ano, saldo_inicial)
        saldo_existente = session.query(SaldoFinal).filter(
            SaldoFinal.mes == mes,
            SaldoFinal.ano == ano,
            SaldoFinal.id_usuario == st.session_state['user_id']
        ).first()

        if saldo_existente:
            saldo_existente.saldo = saldo_final
        else:
            saldo_novo = SaldoFinal(
                mes=mes,
                ano=ano,
                saldo=saldo_final,
                id_usuario=st.session_state['user_id']
            )
            session.add(saldo_novo)
    session.commit()

def dados_relatorio(mes=None):
    dados = []
    saldo_anterior = 0

    configuracao = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    ano_vigente = configuracao.ano_vigente if configuracao else datetime.now().year

    outras_receitas = float(session.query(db.func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Receitas',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0)

    aci_recebida = float(session.query(db.func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Recebida',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0)

    outras_despesas = float(session.query(db.func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'Outras Despesas',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0)

    aci_enviada = float(session.query(db.func.sum(Lancamento.valor)).filter(
        Lancamento.tipo == 'ACI Enviada',
        extract('year', Lancamento.data) == ano_vigente,
        Lancamento.id_usuario == st.session_state['user_id']
    ).scalar() or 0)

    total_receitas = outras_receitas + aci_recebida
    total_despesas = outras_despesas + aci_enviada
    saldo_final_ano = (configuracao.saldo_inicial or 0) + total_receitas - total_despesas if configuracao else total_receitas - total_despesas

    meses = range(1, 13) if mes is None else [mes]

    for mes_atual in meses:
        saldo_inicial = saldo_anterior if mes_atual > 1 else float(configuracao.saldo_inicial or 0) if configuracao else 0

        entradas = float(session.query(db.func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Receitas', 'ACI Recebida']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == st.session_state['user_id']
        ).scalar() or 0)

        saidas = float(session.query(db.func.sum(Lancamento.valor)).filter(
            Lancamento.tipo.in_(['Outras Despesas', 'ACI Enviada']),
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == st.session_state['user_id']
        ).scalar() or 0)

        saldo_final = saldo_inicial + entradas - saidas
        saldo_anterior = saldo_final

        lancamentos = session.query(Lancamento).filter(
            extract('month', Lancamento.data) == mes_atual,
            extract('year', Lancamento.data) == ano_vigente,
            Lancamento.id_usuario == st.session_state['user_id']
        ).all()

        dados.append({
            'mes': mes_atual,
            'saldo_inicial': saldo_inicial,
            'entradas': entradas,
            'saidas': saidas,
            'saldo_final': saldo_final,
            'saldo_final_ano': saldo_final_ano,
            'lancamentos': lancamentos,
            'configuracao': configuracao,
            'outras_receitas': outras_receitas,
            'aci_recebida': aci_recebida,
            'outras_despesas': outras_despesas,
            'aci_enviada': aci_enviada,
            'total_receitas': total_receitas,
            'total_despesas': total_despesas,
            'ano_vigente': ano_vigente
        })

    return dados

def buscar_lancamentos(ano=None, mes=None):
    """Retorna os lançamentos filtrados por ano, mês e usuário logado."""
    query = session.query(Lancamento)
    query = query.filter(Lancamento.id_usuario == st.session_state['user_id'])

    if ano:
        query = query.filter(extract('year', Lancamento.data) == ano)
    if mes:
        query = query.filter(extract('month', Lancamento.data) == mes)

    return query.all()

def exportar_relatorio(mes=None):
    dados = dados_relatorio(mes)
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year

    class PDFWithFooter(FPDF):
        def footer(self):
            self.set_y(-15)
            self.set_font("Arial", size=8)
            self.set_text_color(128, 128, 128)  # Cinza
            self.cell(0, 10, "Desenvolvido por Miquéias Teles | © 2025 Todos os direitos reservados", 0, 0, 'C')

    pdf = PDFWithFooter()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    dados_config = dados[0].get('configuracao', {}) if dados else {}

    logo_path = os.path.join('static', "Logos/Marca_UMP 02.png")
    try:
        pdf.image(logo_path, x=80, y=8, w=50)
    except:
        pass

    pdf.ln(18)

    pdf.set_text_color(28, 30, 62)
    pdf.set_font("Arial", style='B', size=14)
    pdf.cell(190, 10, txt=f"RELATÓRIO FINANCEIRO {ano}{f' - Mês {mes}' if mes else ''}", ln=True, align='C')
    pdf.ln(0)

    pdf.set_font("Arial", style='B', size=12)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(10)

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)
    pdf.cell(190, 8, txt="Informações de Cabeçalho", ln=True, align='C', fill=True)
    pdf.ln(5)

    largura_campo = 95
    largura_valor = 95
    altura_celula = 8

    pdf.set_text_color(28, 30, 62)
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(201, 203, 231)
    pdf.cell(largura_campo, altura_celula, "Campos", border=1, align='C', fill=True)
    pdf.cell(largura_valor, altura_celula, "Informações", border=1, align='C', fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", size=11)
    campos = [
        ("UMP/Federação:", dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else "Não definido"),
        ("Federação/Sínodo:", dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else "Não definido"),
        ("Ano Vigente:", str(dados_config.ano_vigente if hasattr(dados_config, 'ano_vigente') else "Não definido")),
        ("Sócios Ativos:", str(dados_config.socios_ativos if hasattr(dados_config, 'socios_ativos') else "Não definido")),
        ("Sócios Cooperadores:", str(dados_config.socios_cooperadores if hasattr(dados_config, 'socios_cooperadores') else "Não definido")),
        ("Tesoureiro Responsável:", dados_config.tesoureiro_responsavel if hasattr(dados_config, 'tesoureiro_responsavel') else "Não definido"),
    ]

    for campo, valor in campos:
        pdf.cell(largura_campo, altura_celula, campo, border=1)
        pdf.cell(largura_valor, altura_celula, valor, border=1)
        pdf.ln()

    pdf.ln(5)

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)
    pdf.cell(190, 8, txt="Resumo Financeiro", ln=True, align='C', fill=True)
    pdf.ln(5)

    resumo = dados[0] if dados else {}

    pdf.set_text_color(28, 30, 62)
    pdf.set_font("Arial", style='B', size=11)
    pdf.set_fill_color(201, 203, 231)
    pdf.cell(largura_campo, altura_celula, "Receitas", border=1, align='C', fill=True)
    pdf.cell(largura_valor, altura_celula, "Despesas", border=1, align='C', fill=True)
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", size=11)
    resumo_financeiro = [
        (f"Outras Receitas: R$ {locale.format_string('%.2f', resumo.get('outras_receitas', 0.00), grouping=True)}",
         f"Outras Despesas: R$ {locale.format_string('%.2f', resumo.get('outras_despesas', 0.00), grouping=True)}"),
        (f"ACI Recebida: R$ {locale.format_string('%.2f', resumo.get('aci_recebida', 0.00), grouping=True)}",
         f"ACI Enviada: R$ {locale.format_string('%.2f', resumo.get('aci_enviada', 0.00), grouping=True)}")
    ]

    for receita, despesa in resumo_financeiro:
        pdf.cell(largura_campo, altura_celula, receita, border=1)
        pdf.cell(largura_valor, altura_celula, despesa, border=1)
        pdf.ln()

    pdf.ln(5)

    pdf.set_font("Arial", style='B', size=12)
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(largura_campo, 10, f"Total de Receitas: R$ {locale.format_string('%.2f', resumo.get('total_receitas', 0.00), grouping=True)}", border=1, fill=True)
    pdf.cell(largura_valor, 10, f"Total de Despesas: R$ {locale.format_string('%.2f', resumo.get('total_despesas', 0.00), grouping=True)}", border=1, fill=True)
    pdf.ln(10)

    pdf.ln(20)
    pdf.set_font("Arial", size=12)
    assinatura_texto = "Assinatura do Tesoureiro"
    largura_assinatura = pdf.get_string_width(assinatura_texto) + 10
    pdf.set_x((pdf.w - largura_assinatura) / 2)
    pdf.cell(largura_assinatura, 10, assinatura_texto, align='C')
    pdf.line((pdf.w - largura_assinatura) / 2, pdf.get_y() + 3, (pdf.w + largura_assinatura) / 2, pdf.get_y() + 3)
    pdf.ln(20)

    assinatura_texto = "Assinatura do Presidente"
    largura_assinatura = pdf.get_string_width(assinatura_texto) + 10
    pdf.set_x((pdf.w - largura_assinatura) / 2)
    pdf.cell(largura_assinatura, 10, assinatura_texto, align='C')
    pdf.line((pdf.w - largura_assinatura) / 2, pdf.get_y() + 3, (pdf.w + largura_assinatura) / 2, pdf.get_y() + 3)
    pdf.ln(30)

    pdf.set_font("Arial", size=10)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(0)
    pdf.set_font("Arial", size=9)
    pdf.cell(190, 10, txt="''Alegres na Esperança, Fortes na Fé, Dedicados no Amor, Unidos no Trabalho''", ln=True, align='C')
    pdf.ln(10)

    meses = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
        7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }

    for d in dados:
        mes_nome = meses.get(d['mes'], f"Mês {d['mes']}")
        pdf.set_text_color(28, 30, 62)
        pdf.set_font("Arial", style='B', size=12)
        pdf.cell(190, 10, txt=f"Mês {d['mes']} - {mes_nome} {ano}", ln=True, align='C')
        pdf.ln(5)

        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", style='B', size=10)
        pdf.set_fill_color(28, 30, 62)
        pdf.cell(55, 10, "Saldo Inicial", border=1, align='C', fill=True)
        pdf.cell(40, 10, "Entradas", border=1, align='C', fill=True)
        pdf.cell(40, 10, "Saídas", border=1, align='C', fill=True)
        pdf.cell(55, 10, "Saldo Final", border=1, align='C', fill=True)
        pdf.ln()

        pdf.set_text_color(28, 30, 62)
        pdf.set_font("Arial", size=11)
        pdf.cell(55, 10, f"R$ {locale.format_string('%.2f', d['saldo_inicial'], grouping=True)}", border=1, align='C')
        pdf.cell(40, 10, f"R$ {locale.format_string('%.2f', d['entradas'], grouping=True)}", border=1, align='C')
        pdf.cell(40, 10, f"R$ {locale.format_string('%.2f', d['saidas'], grouping=True)}", border=1, align='C')
        pdf.cell(55, 10, f"R$ {locale.format_string('%.2f', d['saldo_final'], grouping=True)}", border=1, align='C')
        pdf.ln(15)

        pdf.set_font("Arial", style='B', size=10)
        pdf.set_fill_color(200, 200, 200)
        pdf.cell(35, 10, "Data", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Tipo", border=1, align='C', fill=True)
        pdf.cell(65, 10, "Descrição", border=1, align='C', fill=True)
        pdf.cell(35, 10, "Valor", border=1, align='C', fill=True)
        pdf.cell(20, 10, "Cód.", border=1, align='C', fill=True)
        pdf.ln()

        pdf.set_font("Arial", size=10)
        for lanc in d['lancamentos']:
            pdf.cell(35, 10, txt=lanc.data.strftime('%d/%m/%Y'), border=1, align='C')
            pdf.cell(35, 10, txt=lanc.tipo, border=1, align='C')
            pdf.cell(65, 10, txt=lanc.descricao, border=1, align='C')
            pdf.cell(35, 10, txt=f"R$ {locale.format_string('%.2f', lanc.valor, grouping=True)}", border=1, align='C')
            pdf.cell(20, 10, txt=str(lanc.id), border=1, align='C')
            pdf.ln()

        pdf.ln(5)

    pdf_file = f"relatorio_{ano}_id_usuario_{st.session_state['user_id']}.pdf"
    pdf_path = os.path.join(RELATORIOS_DIR, pdf_file)
    pdf.output(pdf_path)
    return pdf_path

def exportar_comprovantes(ano=None, mes=None):
    dados = dados_relatorio(mes) if mes else dados_relatorio()
    ano = dados[0]['ano_vigente'] if dados else datetime.now().year
    dados_config = dados[0].get('configuracao', {}) if dados else {}
    lancamentos = buscar_lancamentos(ano, mes)

    class PDFWithFooter(FPDF):
        def footer(self):
            self.set_y(-15)
            self.set_font("Arial", size=8)
            self.set_text_color(128, 128, 128)  # Cinza
            self.cell(0, 10, "Desenvolvido por Miquéias Teles | © 2025 Todos os direitos reservados", 0, 0, 'C')

    pdf = PDFWithFooter()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.set_text_color(28, 30, 62)
    pdf.set_font("Arial", style='B', size=14)
    pdf.cell(190, 10, f"RELATÓRIO DE COMPROVANTES - {ano if ano else 'Todos'}{f' Mês {mes}' if mes else ''}", ln=True, align='C')
    pdf.ln(0)

    pdf.set_font("Arial", style='B', size=12)
    campo = f"{dados_config.ump_federacao if hasattr(dados_config, 'ump_federacao') else 'Não definido'} - {dados_config.federacao_sinodo if hasattr(dados_config, 'federacao_sinodo') else 'Não definido'}"
    pdf.cell(190, 10, campo, ln=True, align='C')
    pdf.ln(10)

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", style='B', size=14)
    pdf.set_fill_color(28, 30, 62)
    pdf.cell(190, 8, txt="Relação de Comprovantes", ln=True, align='C', fill=True)
    pdf.ln(5)

    pdf.set_text_color(28, 30, 62)
    pdf.set_font("Arial", style='B', size=10)
    pdf.cell(15, 10, "Cód.", border=1, align='C')
    pdf.cell(30, 10, "Data", border=1, align='C')
    pdf.cell(80, 10, "Descrição", border=1, align='C')
    pdf.cell(30, 10, "Valor", border=1, align='C')
    pdf.cell(35, 10, "Comprovante", border=1, align='C')
    pdf.ln()

    pdf.set_font("Arial", size=10)
    for lanc in lancamentos:
        pdf.cell(15, 10, str(lanc.id), border=1, align='C')
        pdf.cell(30, 10, txt=lanc.data.strftime('%d/%m/%Y'), border=1, align='C')
        pdf.cell(80, 10, lanc.descricao, border=1, align='L')
        pdf.cell(30, 10, f"R$ {locale.format_string('%.2f', lanc.valor, grouping=True)}", border=1, align='C')
        pdf.cell(35, 10, "Anexado" if lanc.comprovante else "Não anexado", border=1, align='C')
        pdf.ln()

    pdf.ln(10)

    for lanc in lancamentos:
        if lanc.comprovante:
            comprovante_path = lanc.comprovante if lanc.comprovante.startswith(UPLOAD_FOLDER) else os.path.join(UPLOAD_FOLDER, lanc.comprovante)

            if os.path.exists(comprovante_path):
                file_extension = comprovante_path.lower().split('.')[-1]
                if file_extension in ['jpg', 'jpeg', 'png']:
                    try:
                        pdf.add_page()
                        pdf.set_font("Arial", style='B', size=12)
                        pdf.cell(190, 10, f"Comprovante - Cód. {lanc.id}", ln=True, align='C')
                        pdf.ln(5)

                        image = Image.open(comprovante_path)
                        img_width, img_height = image.size

                        max_width = 190
                        max_height = 250
                        ratio = min(max_width / img_width, max_height / img_height)
                        new_width = img_width * ratio
                        new_height = img_height * ratio

                        pdf.image(comprovante_path, x=10, y=30, w=new_width, h=new_height)

                    except Exception as e:
                        pdf.cell(190, 10, f"Erro ao carregar imagem: {str(e)}", ln=True, align='C')
                else:
                    pdf.add_page()
                    pdf.set_font("Arial", style='B', size=12)
                    pdf.cell(190, 10, f"Comprovante - ID {lanc.id} (Formato de arquivo não suportado)", ln=True, align='C')
            else:
                pdf.add_page()
                pdf.set_font("Arial", style='B', size=12)
                pdf.cell(190, 10, f"Comprovante - ID {lanc.id} (Arquivo não encontrado)", ln=True, align='C')

    pdf_file = f"comprovantes_{ano}_id_usuario_{st.session_state['user_id']}.pdf"
    pdf_path = os.path.join(RELATORIOS_DIR, pdf_file)
    pdf.output(pdf_path)
    return pdf_path

# Funções de recuperação de senha
def verificar_email_no_banco(email):
    user = session.query(Configuracao).filter_by(email=email).first()
    if user:
        return user.id_usuario
    return None

def gerar_senha_aleatoria():
    return str(random.randint(100000, 999999))  # Gera uma senha numérica de 6 dígitos

def atualizar_senha_no_banco(user_id, nova_senha):
    usuario = session.query(Usuario).filter_by(id=user_id).first()
    if usuario:
        usuario.senha = nova_senha
        session.commit()
        return True
    return False

def validar_email(email):
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(email_regex, email) is not None

def enviar_email_sendinblue(email_destinatario, nova_senha):
    api_key = os.getenv("SENDINBLUE_API_KEY")
    if not api_key:
        st.error("Erro: Chave da API Sendinblue não configurada. Defina a variável de ambiente SENDINBLUE_API_KEY.")
        return False
    
    url = "https://api.sendinblue.com/v3/smtp/email"
    
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json"
    }
    
    data = {
        "sender": {"email": "suporteumpfinanceiro@gmail.com"},
        "to": [{"email": email_destinatario}],
        "subject": "Recuperação de Senha",
        "textContent": f"Olá!\n\nSua nova senha é: {nova_senha}\n\nEste é um e-mail automático. Por favor, não responda a esta mensagem."
    }

    try:
        resposta = requests.post(url, json=data, headers=headers)
        if resposta.status_code in [200, 201]:
            st.success("E-mail enviado com sucesso!")
            return True
        else:
            st.error(f"Erro ao enviar o e-mail: {resposta.status_code} - {resposta.text}")
            return False
    except Exception as e:
        st.error(f"Erro ao tentar enviar o e-mail: {str(e)}")
        return False

def recuperar_senha_page():
    st.title("Recuperar Senha")
    
    with st.form(key='recuperar_senha_form'):
        email = st.text_input("Digite seu e-mail")
        submit_button = st.form_submit_button(label="Enviar nova senha")
        
        if submit_button:
            if not email:
                st.error("Por favor, insira um e-mail.")
            elif not validar_email(email):
                st.error("Formato de e-mail inválido.")
            else:
                user_id = verificar_email_no_banco(email)
                if user_id:
                    nova_senha = gerar_senha_aleatoria()
                    if atualizar_senha_no_banco(user_id, nova_senha):
                        if enviar_email_sendinblue(email, nova_senha):
                            st.success("Uma nova senha foi enviada para o seu e-mail. Verifique sua caixa de entrada (ou spam).")
                        else:
                            st.error("Falha ao enviar o e-mail. Tente novamente mais tarde.")
                    else:
                        st.error("Erro ao atualizar a senha no banco de dados.")
                else:
                    st.error("E-mail não encontrado no sistema.")
    
    if st.button("Voltar ao Login"):
        st.session_state['recuperar_senha'] = False
        st.rerun()

def login_page():
    if st.session_state['recuperar_senha']:
        recuperar_senha_page()
    else:
        st.title("Login")
        with st.form(key='login_form'):
            username = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            submit_button = st.form_submit_button(label="Entrar")
            if submit_button:
                usuario = session.query(Usuario).filter_by(username=username).first()
                if usuario and usuario.senha == senha:
                    st.session_state['logged_in'] = True
                    st.session_state['current_user'] = usuario.username
                    st.session_state['user_id'] = usuario.id
                    st.success("Login bem-sucedido!")
                    st.rerun()
                else:
                    st.error("Credenciais inválidas")
        
        # Botão para recuperar senha
        if st.button("Esqueceu sua senha?"):
            st.session_state['recuperar_senha'] = True
            st.rerun()

def logout():
    st.session_state['logged_in'] = False
    st.session_state['current_user'] = None
    st.session_state['user_id'] = None
    st.session_state['edit_lancamento_id'] = None
    st.session_state['selected_page'] = None
    st.session_state['recuperar_senha'] = False  # Reseta o estado de recuperação
    st.success("Logout realizado!")
    st.rerun()

def index_page():
    st.title(f"Bem-vindo, {st.session_state['current_user']}!")
    
    config = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    if not config:
        config = Configuracao(
            ump_federacao="UMP Local",
            federacao_sinodo="Sinodal Exemplo",
            ano_vigente=2025,
            saldo_inicial=0,
            id_usuario=st.session_state['user_id']
        )
        session.add(config)
        session.commit()

    outras_receitas = session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Receitas").scalar() or 0
    aci_recebida = session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Recebida").scalar() or 0
    outras_despesas = session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "Outras Despesas").scalar() or 0
    aci_enviada = session.query(db.func.sum(Lancamento.valor)).filter(Lancamento.tipo == "ACI Enviada").scalar() or 0

    receitas = outras_receitas + aci_recebida
    despesas = outras_despesas + aci_enviada
    saldo_final = (config.saldo_inicial or 0) + receitas - despesas

    saldo_formatado = format_currency_brl(config.saldo_inicial or 0)
    receitas_formatadas = format_currency_brl(receitas)
    despesas_formatadas = format_currency_brl(despesas)
    saldo_final_formatado = format_currency_brl(saldo_final)
    outras_receitas_formatadas = format_currency_brl(outras_receitas)
    aci_recebida_formatada = format_currency_brl(aci_recebida)
    outras_despesas_formatadas = format_currency_brl(outras_despesas)
    aci_enviada_formatada = format_currency_brl(aci_enviada)

    st.subheader(f"Dashboard Financeiro - {config.ano_vigente}")
    st.write(f"UMP Federação: {config.ump_federacao}")
    st.write(f"Federação Sínodo: {config.federacao_sinodo}")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Saldo Inicial", saldo_formatado)
    with col2:
        st.metric("Receitas", receitas_formatadas)
    with col3:
        st.metric("Despesas", despesas_formatadas)
    
    st.metric("Saldo Final", saldo_final_formatado)
    
    st.subheader("Detalhamento")
    st.write(f"Outras Receitas: {outras_receitas_formatadas}")
    st.write(f"ACI Recebida: {aci_recebida_formatada}")
    st.write(f"Outras Despesas: {outras_despesas_formatadas}")
    st.write(f"ACI Enviada: {aci_enviada_formatada}")

def configuracoes_page():
    st.title("Configurações")
    
    config = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    if not config:
        config = Configuracao(id_usuario=st.session_state['user_id'])
        session.add(config)
        session.commit()

    with st.form(key='config_form'):
        ump_federacao = st.text_input("UMP Federação", value=config.ump_federacao or "UMP Local")
        federacao_sinodo = st.text_input("Federação Sínodo", value=config.federacao_sinodo or "Sinodal Exemplo")
        ano_vigente = st.number_input("Ano Vigente", min_value=2000, max_value=2100, value=config.ano_vigente or 2025)
        socios_ativos = st.text_input("Sócios Ativos", value=config.socios_ativos or "")
        socios_cooperadores = st.text_input("Sócios Cooperadores", value=config.socios_cooperadores or "")
        tesoureiro_responsavel = st.text_input("Tesoureiro Responsável", value=config.tesoureiro_responsavel or "")
        email = st.text_input("E-mail", value=config.email or "")
        saldo_inicial = st.text_input(
            "Saldo Inicial (ex: 1.234,56)",
            value=format_currency_brl(config.saldo_inicial or 0, include_symbol=False)
        )

        submit_button = st.form_submit_button(label="Salvar")

        if submit_button:
            if verificar_email_existente(email, st.session_state['user_id']):
                st.error("Este e-mail já está cadastrado.")
            else:
                config.ump_federacao = ump_federacao
                config.federacao_sinodo = federacao_sinodo
                config.ano_vigente = int(ano_vigente)
                config.socios_ativos = socios_ativos
                config.socios_cooperadores = socios_cooperadores
                config.tesoureiro_responsavel = tesoureiro_responsavel
                config.email = email

                try:
                    saldo_inicial_float = float(saldo_inicial.replace('.', '').replace(',', '.'))
                except ValueError:
                    saldo_inicial_float = 0.0
                config.saldo_inicial = saldo_inicial_float

                session.query(SaldoFinal).filter_by(id_usuario=st.session_state['user_id']).update({"ano": config.ano_vigente})
                session.commit()
                recalcular_saldos_finais()
                st.success("Configurações salvas com sucesso!")
                st.rerun()

    saldo_formatado = format_currency_brl(config.saldo_inicial or 0, grouping=True)
    st.write(f"Saldo Inicial Atual: {saldo_formatado}")

def mes_page():
    st.title("Relatório Mensal")
    
    config = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    ano_vigente = config.ano_vigente if config else datetime.now().year
    
    mes = st.selectbox("Selecione o Mês", range(1, 13), format_func=lambda x: f"{x:02d}")
    ano = ano_vigente

    saldo_inicial = obter_saldo_inicial(mes, ano)
    lancamentos = session.query(Lancamento).filter(
        Lancamento.data.like(f"{ano}-{mes:02d}%"),
        Lancamento.id_usuario == st.session_state['user_id']
    ).all()

    entradas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Receitas', 'ACI Recebida'])
    saidas = sum(l.valor for l in lancamentos if l.tipo in ['Outras Despesas', 'ACI Enviada'])
    saldo = saldo_inicial + entradas - saidas

    saldo_inicial_formatado = locale.currency(saldo_inicial, grouping=True)
    entradas_formatado = locale.currency(entradas, grouping=True)
    saidas_formatado = locale.currency(saidas, grouping=True)
    saldo_formatado = locale.currency(saldo, grouping=True)

    st.subheader(f"Relatório de {mes:02d}/{ano}")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Saldo Inicial", saldo_inicial_formatado)
    with col2:
        st.metric("Entradas", entradas_formatado)
    with col3:
        st.metric("Saídas", saidas_formatado)
    st.metric("Saldo Final", saldo_formatado)

    st.subheader("Lançamentos")
    for lancamento in lancamentos:
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.write(f"{lancamento.data.strftime('%d/%m/%Y')} - {lancamento.tipo} - {lancamento.descricao} - {locale.currency(lancamento.valor, grouping=True)}")
            if lancamento.comprovante:
                st.image(lancamento.comprovante, caption="Comprovante", width=200)
        with col2:
            if st.button("Editar", key=f"edit_{lancamento.id}"):
                st.session_state['edit_lancamento_id'] = lancamento.id
                st.rerun()
        with col3:
            if st.button("Excluir", key=f"delete_{lancamento.id}"):
                if lancamento.comprovante and os.path.exists(lancamento.comprovante):
                    os.remove(lancamento.comprovante)
                session.delete(lancamento)
                session.commit()
                saldo_inicial = obter_saldo_inicial(mes, ano)
                salvar_saldo_final(mes, ano, saldo_inicial)
                recalcular_saldos_finais()
                st.success("Lançamento excluído com sucesso!")
                st.rerun()

    if st.session_state['edit_lancamento_id']:
        editar_lancamento_page(mes, ano)

    recalcular_saldos_finais()

    st.subheader("Exportar Relatório")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Exportar Mês Selecionado"):
            pdf_path = exportar_relatorio(mes)
            with open(pdf_path, "rb") as file:
                st.download_button(
                    label="Baixar Relatório do Mês",
                    data=file.read(),
                    file_name=f"relatorio_{ano}_mes_{mes}_id_usuario_{st.session_state['user_id']}.pdf",
                    mime="application/pdf"
                )
    with col2:
        if st.button("Exportar Ano Completo"):
            pdf_path = exportar_relatorio()
            with open(pdf_path, "rb") as file:
                st.download_button(
                    label="Baixar Relatório do Ano",
                    data=file.read(),
                    file_name=f"relatorio_{ano}_id_usuario_{st.session_state['user_id']}.pdf",
                    mime="application/pdf"
                )

    st.subheader("Exportar Comprovantes")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Exportar Comprovantes do Mês"):
            pdf_path = exportar_comprovantes(ano, mes)
            with open(pdf_path, "rb") as file:
                st.download_button(
                    label="Baixar Comprovantes do Mês",
                    data=file.read(),
                    file_name=f"comprovantes_{ano}_mes_{mes}_id_usuario_{st.session_state['user_id']}.pdf",
                    mime="application/pdf"
                )
    with col2:
        if st.button("Exportar Comprovantes do Ano"):
            pdf_path = exportar_comprovantes(ano)
            with open(pdf_path, "rb") as file:
                st.download_button(
                    label="Baixar Comprovantes do Ano",
                    data=file.read(),
                    file_name=f"comprovantes_{ano}_id_usuario_{st.session_state['user_id']}.pdf",
                    mime="application/pdf"
                )

def lancamentos_page():
    st.title("Lançamentos")
    
    config = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    ano_atual = config.ano_vigente if config else datetime.now().year
    st.write(f"Ano Atual: {ano_atual}")

def adicionar_lancamento_page():
    st.title("Adicionar Lançamento")
    
    config = session.query(Configuracao).filter_by(id_usuario=st.session_state['user_id']).first()
    ano = config.ano_vigente if config else datetime.now().year

    with st.form(key='add_lancamento_form'):
        mes = st.selectbox("Mês", range(1, 13), format_func=lambda x: f"{x:02d}")
        data = st.date_input("Data", value=datetime.now())
        tipo = st.selectbox("Tipo", ["Outras Receitas", "ACI Recebida", "Outras Despesas", "ACI Enviada"])
        descricao = st.text_input("Descrição")
        valor = st.text_input("Valor (ex: 1234,56)")
        comprovante = st.file_uploader("Comprovante", type=ALLOWED_EXTENSIONS)
        
        submit_button = st.form_submit_button(label="Adicionar")

        if submit_button:
            if not data or not tipo or not descricao or not valor:
                st.error("Erro: Todos os campos devem ser preenchidos.")
            else:
                try:
                    valor_float = float(valor.replace(',', '.'))
                    comprovante_path = None

                    if comprovante:
                        filename = secure_filename(comprovante.name)
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        with open(file_path, "wb") as f:
                            f.write(comprovante.getbuffer())

                        if filename.lower().endswith('.pdf'):
                            images = convert_from_path(file_path)
                            if images:
                                image_filename = filename.replace('.pdf', '.jpg')
                                image_path = os.path.join(UPLOAD_FOLDER, image_filename)
                                images[0].save(image_path, 'JPEG')
                                os.remove(file_path)
                                comprovante_path = image_path
                        else:
                            comprovante_path = file_path

                    lancamento = Lancamento(
                        data=data,
                        tipo=tipo,
                        descricao=descricao,
                        valor=valor_float,
                        comprovante=comprovante_path,
                        id_usuario=st.session_state['user_id']
                    )
                    session.add(lancamento)
                    session.commit()

                    saldo_inicial = obter_saldo_inicial(mes, ano)
                    salvar_saldo_final(mes, ano, saldo_inicial)
                    recalcular_saldos_finais()
                    st.success("Lançamento adicionado com sucesso!")
                    st.rerun()

                except ValueError:
                    st.error("Erro: Valor inválido.")

def editar_lancamento_page(mes, ano):
    lancamento = session.query(Lancamento).filter_by(
        id=st.session_state['edit_lancamento_id'],
        id_usuario=st.session_state['user_id']
    ).first()

    if not lancamento:
        st.error("Lançamento não encontrado ou você não tem permissão para editá-lo!")
        st.session_state['edit_lancamento_id'] = None
        return

    st.subheader(f"Editando Lançamento ID: {lancamento.id}")
    with st.form(key='edit_lancamento_form'):
        data = st.date_input("Data", value=lancamento.data)
        tipo = st.selectbox("Tipo", ["Outras Receitas", "ACI Recebida", "Outras Despesas", "ACI Enviada"], index=["Outras Receitas", "ACI Recebida", "Outras Despesas", "ACI Enviada"].index(lancamento.tipo))
        descricao = st.text_input("Descrição", value=lancamento.descricao)
        valor = st.text_input("Valor (ex: 1234,56)", value=str(lancamento.valor).replace('.', ','))
        comprovante = st.file_uploader("Novo Comprovante", type=ALLOWED_EXTENSIONS)

        submit_button = st.form_submit_button(label="Salvar")
        cancel_button = st.form_submit_button(label="Cancelar")

        if submit_button:
            try:
                valor_float = float(valor.replace(',', '.'))
                comprovante_path = lancamento.comprovante

                if comprovante:
                    filename = secure_filename(comprovante.name)
                    file_path = os.path.join(UPLOAD_FOLDER, filename)
                    with open(file_path, "wb") as f:
                        f.write(comprovante.getbuffer())

                    if filename.lower().endswith('.pdf'):
                        images = convert_from_path(file_path)
                        if images:
                            image_filename = filename.replace('.pdf', '.jpg')
                            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
                            images[0].save(image_path, 'JPEG')
                            os.remove(file_path)
                            comprovante_path = image_path
                    else:
                        comprovante_path = file_path

                lancamento.data = data
                lancamento.tipo = tipo
                lancamento.descricao = descricao
                lancamento.valor = valor_float
                if comprovante and lancamento.comprovante and os.path.exists(lancamento.comprovante):
                    os.remove(lancamento.comprovante)
                if comprovante:
                    lancamento.comprovante = comprovante_path

                session.commit()
                recalcular_saldos_finais()
                st.success("Lançamento atualizado com sucesso!")
                st.session_state['edit_lancamento_id'] = None
                st.rerun()

            except ValueError:
                st.error("Erro: Valor inválido.")

        if cancel_button:
            st.session_state['edit_lancamento_id'] = None
            st.rerun()

def admin_relatorios_page():
    st.title("Consulta de Relatórios - Administrador")
    
    administradores = carregar_administradores()
    if st.session_state['user_id'] not in administradores:
        st.error("Você não tem permissão para acessar esta página.")
        return
    
    usuarios_autorizados = administradores.get(st.session_state['user_id'], [])
    st.subheader("Usuários Autorizados")
    for id_usuario, ump_federacao in usuarios_autorizados:
        st.write(f"ID: {id_usuario} - UMP Federação: {ump_federacao}")

    st.subheader("Buscar Relatório")
    usuarios_autorizados_db = session.query(Configuracao.id_usuario, Configuracao.ump_federacao).filter(
        Configuracao.id_usuario.in_([usuario[0] for usuario in usuarios_autorizados])
    ).all()

    usuarios_autorizados_db = [
        {"id_usuario": usuario.id_usuario, "ump_federacao": usuario.ump_federacao if usuario.ump_federacao else "Nome não disponível"}
        for usuario in usuarios_autorizados_db
    ]

    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    with st.form(key='buscar_relatorio_form'):
        ano = st.selectbox("Ano", anos)
        usuario_options = {f"{u['ump_federacao']} (ID: {u['id_usuario']})": u['id_usuario'] for u in usuarios_autorizados_db}
        usuario_selecionado = st.selectbox("Usuário", list(usuario_options.keys()))
        usuario_id = usuario_options[usuario_selecionado]
        
        submit_button = st.form_submit_button(label="Buscar")

    if submit_button:
        relatorio_nome = f"relatorio_{ano}_id_usuario_{usuario_id}.pdf"
        relatorio_path = os.path.join(RELATORIOS_DIR, relatorio_nome)

        st.write(f"Procurando relatório em: {relatorio_path}")  # Depuração

        if os.path.exists(relatorio_path):
            try:
                st.write("Convertendo PDF para imagens...")  # Depuração
                images = convert_from_path(relatorio_path, dpi=200)  # Ajuste DPI se necessário
                for i, image in enumerate(images):
                    st.image(image, caption=f"Página {i+1} do Relatório", use_container_width=True)
                
                with open(relatorio_path, "rb") as file:
                    st.download_button(
                        label="Baixar Relatório",
                        data=file.read(),
                        file_name=relatorio_nome,
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Erro ao processar o PDF: {str(e)}")
                st.write("Detalhes do erro:")
                st.text(traceback.format_exc())  # Mostra stack trace completo para depuração
        else:
            st.warning(f"Relatório para o ano {ano} do usuário {usuario_id} não encontrado em {relatorio_path}.")

def admin_comprovantes_page():
    st.title("Consulta de Comprovantes - Administrador")
    
    administradores = carregar_administradores()
    if st.session_state['user_id'] not in administradores:
        st.error("Você não tem permissão para acessar esta página.")
        return
    
    usuarios_autorizados = administradores.get(st.session_state['user_id'], [])
    st.subheader("Usuários Autorizados")
    for id_usuario, ump_federacao in usuarios_autorizados:
        st.write(f"ID: {id_usuario} - UMP Federação: {ump_federacao}")

    st.subheader("Buscar Comprovantes")
    usuarios_autorizados_db = session.query(Configuracao.id_usuario, Configuracao.ump_federacao).filter(
        Configuracao.id_usuario.in_([usuario[0] for usuario in usuarios_autorizados])
    ).all()

    usuarios_autorizados_db = [
        {"id_usuario": usuario.id_usuario, "ump_federacao": usuario.ump_federacao if usuario.ump_federacao else "Nome não disponível"}
        for usuario in usuarios_autorizados_db
    ]

    ano_atual = datetime.now().year
    anos = list(range(ano_atual - 4, ano_atual + 1))

    with st.form(key='buscar_comprovantes_form'):
        ano = st.selectbox("Ano", anos)
        usuario_options = {f"{u['ump_federacao']} (ID: {u['id_usuario']})": u['id_usuario'] for u in usuarios_autorizados_db}
        usuario_selecionado = st.selectbox("Usuário", list(usuario_options.keys()))
        usuario_id = usuario_options[usuario_selecionado]
        
        submit_button = st.form_submit_button(label="Buscar")

    if submit_button:
        comprovante_nome = f"comprovantes_{ano}_id_usuario_{usuario_id}.pdf"
        comprovante_path = os.path.join(RELATORIOS_DIR, comprovante_nome)

        st.write(f"Procurando comprovantes em: {comprovante_path}")  # Depuração

        if os.path.exists(comprovante_path):
            try:
                st.write("Convertendo PDF para imagens...")  # Depuração
                images = convert_from_path(comprovante_path, dpi=200)  # Ajuste DPI se necessário
                for i, image in enumerate(images):
                    st.image(image, caption=f"Página {i+1} dos Comprovantes", use_container_width=True)
                
                with open(comprovante_path, "rb") as file:
                    st.download_button(
                        label="Baixar Comprovantes",
                        data=file.read(),
                        file_name=comprovante_nome,
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Erro ao processar o PDF: {str(e)}")
                st.write("Detalhes do erro:")
                st.text(traceback.format_exc())  # Mostra stack trace completo para depuração
        else:
            st.warning(f"Comprovantes para o ano {ano} do usuário {usuario_id} não encontrados em {comprovante_path}.")


def cadastro_usuario_page():
    st.title("Cadastrar Usuário")
    
    administradores = carregar_administradores()
    if st.session_state['user_id'] not in administradores:
        st.error("Você não tem permissão para acessar esta página.")
        return

    with st.form(key='cadastro_usuario_form'):
        username = st.text_input("Nome de Usuário")
        senha = st.text_input("Senha", type="password")
        submit_button = st.form_submit_button(label="Cadastrar")

        if submit_button:
            if not username or not senha:
                st.error("Por favor, preencha todos os campos.")
            else:
                usuario_existente = session.query(Usuario).filter_by(username=username).first()
                if usuario_existente:
                    st.error("Este nome de usuário já está em uso.")
                else:
                    novo_usuario = Usuario(username=username, senha=senha, is_active=1)
                    session.add(novo_usuario)
                    session.commit()

                    id_usuario = novo_usuario.id
                    id_admin = st.session_state['user_id']

                    email = f"{username}@ump.com"
                    configuracao = Configuracao(
                        id_usuario=id_usuario,
                        admin=id_admin,
                        ump_federacao='UMP Federação',
                        federacao_sinodo='Nome do Sinodo',
                        ano_vigente=datetime.now().year,
                        socios_ativos=0,
                        socios_cooperadores=0,
                        tesoureiro_responsavel='Nome do Tesoureiro',
                        saldo_inicial=0.0,
                        email=email
                    )
                    session.add(configuracao)
                    session.commit()

                    for mes in range(1, 13):
                        saldo_final = SaldoFinal(id_usuario=id_usuario, mes=mes, ano=datetime.now().year, saldo=0.0)
                        session.add(saldo_final)
                    session.commit()

                    st.success("Usuário cadastrado com sucesso!")
                    st.rerun()

def alterar_senha_page():
    st.title("Alterar Senha")

    usuario = session.query(Usuario).filter_by(id=st.session_state['user_id']).first()
    if not usuario:
        st.error("Usuário não encontrado!")
        return

    with st.form(key='alterar_senha_form'):
        senha_atual = st.text_input("Senha Atual", type="password")
        nova_senha = st.text_input("Nova Senha", type="password")
        confirmar_senha = st.text_input("Confirmar Nova Senha", type="password")
        submit_button = st.form_submit_button(label="Alterar Senha")

        if submit_button:
            if not senha_atual or not nova_senha or not confirmar_senha:
                st.error("Por favor, preencha todos os campos.")
            else:
                if usuario.senha != senha_atual:
                    st.error("Senha atual incorreta!")
                elif nova_senha != confirmar_senha:
                    st.error("As novas senhas não coincidem!")
                else:
                    usuario.senha = nova_senha
                    session.commit()
                    st.success("Senha alterada com sucesso!")
                    st.rerun()

# Main
def main():
    if not st.session_state['logged_in']:
        login_page()
    else:
        if 'selected_page' not in st.session_state:
            st.session_state['selected_page'] = None

        st.sidebar.title(f"Bem-vindo, {st.session_state['current_user']}")
        administradores = carregar_administradores()
        is_admin = st.session_state['user_id'] in administradores
        
        if is_admin:
            page_options = [
                ("Dashboard", index_page),
                ("Configurações", configuracoes_page),
                ("Relatório Mensal", mes_page),
                ("Lançamentos", lancamentos_page),
                ("Adicionar Lançamento", adicionar_lancamento_page),
                ("Consulta de Relatórios", admin_relatorios_page),
                ("Consulta de Comprovantes", admin_comprovantes_page),
                ("Cadastrar Usuário", cadastro_usuario_page),
                ("Alterar Senha", alterar_senha_page)
            ]
        else:
            page_options = [
                ("Dashboard", index_page),
                ("Configurações", configuracoes_page),
                ("Relatório Mensal", mes_page),
                ("Lançamentos", lancamentos_page),
                ("Adicionar Lançamento", adicionar_lancamento_page),
                ("Alterar Senha", alterar_senha_page)
            ]
        
        for page_name, page_func in page_options:
            if st.sidebar.button(page_name, key=page_name.replace(" ", "_")):
                st.session_state['selected_page'] = page_func
        
        if st.session_state['selected_page']:
            st.session_state['selected_page']()
        else:
            st.write("Selecione uma página na barra lateral para começar.")
        
        if st.sidebar.button("Logout"):
            logout()
        
        st.sidebar.markdown(
            """
            <hr style="border: 1px solid #ccc;">
            <p style="text-align: center; color: #808080; font-size: 12px;">
                Desenvolvido por Miquéias Teles | © 2025 Todos os direitos reservados
            </p>
            """,
            unsafe_allow_html=True
        )

if __name__ == "__main__":
    main()
