from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin



db = SQLAlchemy()

class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    admin = db.Column(db.Integer, nullable=False)
    ump_federacao = db.Column(db.String(100), nullable=False)
    federacao_sinodo = db.Column(db.String(100), nullable=False)
    ano_vigente = db.Column(db.Integer, nullable=False)
    socios_ativos = db.Column(db.Integer, nullable=False)
    socios_cooperadores = db.Column(db.Integer, nullable=False)
    tesoureiro_responsavel = db.Column(db.String(100), nullable=False)
    saldo_inicial = db.Column(db.Float, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)



class Financeiro(db.Model):
    __tablename__ = 'financeiro'
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date)  # Verifique se o nome da coluna é 'data'
    tipo = db.Column(db.String(50))
    valor = db.Column(db.Float)

class Lancamento(db.Model):
    __tablename__ = 'lancamento'  # Garante que SQLAlchemy use a tabela correta

    id = db.Column(db.Integer, primary_key=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(120), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    comprovante = db.Column(db.String(120), nullable=True)

    def __repr__(self):
        return f'<Lancamento {self.id} - {self.descricao}>'

class SaldoFinal(db.Model):
    __tablename__ = 'saldo_final'
    
    id = db.Column(db.Integer, primary_key=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    saldo = db.Column(db.Float, nullable=False)

    def __init__(self, mes, ano, saldo, id_usuario):
        self.mes = mes
        self.ano = ano
        self.saldo = saldo
        self.id_usuario = id_usuario

class Usuario(db.Model, UserMixin):
    __tablename__ = 'usuario'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha = db.Column(db.String(256), nullable=False)  # Mantive o tamanho maior para evitar problemas
    is_active = db.Column(db.Boolean, default=True)  # Para compatibilidade com Flask-Login

    def verificar_senha(self, senha):
        """Verifica a senha sem hash (comparação direta)"""
        return self.senha == senha

    def set_senha(self, senha):
        """Armazena a senha diretamente"""
        self.senha = senha

    def __init__(self, username, senha, is_active=True):
        self.username = username
        self.senha = senha  # Agora a senha é armazenada como texto puro
        self.is_active = is_active

    