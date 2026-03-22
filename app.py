import os
import uuid
import re
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

# --- APP ---
app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- DATABASE CONFIG (RENDER + LOCAL) ---
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///portal_elim_v8.db"

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-key"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    MAX_CONTENT_LENGTH=100 * 1024 * 1024,
    UPLOAD_FOLDER=UPLOAD_FOLDER
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
CORS(app)

# --- MODELS ---

class Unidade(db.Model):
    __tablename__ = "unidades"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False, unique=True)
    cidade = db.Column(db.String(100))
    usuarios = db.relationship("User", backref="unidade", lazy='dynamic')

class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="aluno")

    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"))

    xp = db.Column(db.Integer, default=0)

    progresso = db.relationship("ProgressoAula", backref="user", lazy="dynamic")
    logs = db.relationship("LogAtividade", backref="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Aula(db.Model):
    __tablename__ = "aulas"
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True)
    descricao = db.Column(db.Text)
    url_video = db.Column(db.String(500))
    categoria = db.Column(db.String(100))
    minutos_estimados = db.Column(db.Integer, default=0)
    quiz_data = db.Column(db.JSON)
    status = db.Column(db.String(20), default="publicado")
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por = db.Column(db.Integer, db.ForeignKey('users.id'))

class ProgressoAula(db.Model):
    __tablename__ = "progresso_aulas"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    aula_id = db.Column(db.Integer, db.ForeignKey('aulas.id'))
    concluido = db.Column(db.Boolean, default=False)
    nota_quiz = db.Column(db.Float)
    data_conclusao = db.Column(db.DateTime)

class LogAtividade(db.Model):
    __tablename__ = "logs_atividades"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    acao = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- UTIL ---

def registrar_log(acao):
    if current_user.is_authenticated:
        db.session.add(LogAtividade(user_id=current_user.id, acao=acao))
        db.session.commit()

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                return "Acesso negado", 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

def extrair_id_youtube(url):
    if not url:
        return ""
    regex = r'(?:v=|\/|be\/)([0-9A-Za-z_-]{11})'
    match = re.search(regex, url)
    return match.group(1) if match else url

# --- ROTAS ---

@app.route("/")
def index():
    return "API ONLINE 🚀"

@app.route("/dashboard")
@login_required
def dashboard():
    return f"Bem-vindo {current_user.name}"

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()

    if User.query.filter_by(email=data["email"]).first():
        return jsonify({"error": "Email já existe"}), 400

    user = User(
        name=data["name"],
        email=data["email"],
        is_approved=True
    )
    user.set_password(data["password"])

    db.session.add(user)
    db.session.commit()

    return jsonify({"msg": "Usuário criado"})

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()

    user = User.query.filter_by(email=data["email"]).first()

    if user and user.check_password(data["password"]):
        login_user(user)
        return jsonify({"msg": "logado"})

    return jsonify({"error": "credenciais inválidas"}), 401

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return "logout"

# --- INIT DATABASE (ESSENCIAL PRO RENDER) ---

def setup_initial_data():
    with app.app_context():
        print(">>> Criando banco...")
        db.create_all()

        if not Unidade.query.first():
            db.session.add(Unidade(nome="Campus Central", cidade="SP"))
            db.session.commit()

        if not User.query.filter_by(role="admin").first():
            admin = User(
                name="Admin",
                email="admin@email.com",
                role="admin",
                is_approved=True,
                unidade_id=1
            )
            admin.set_password("123")
            db.session.add(admin)
            db.session.commit()

# 🔥 roda SEMPRE (Render + local)
setup_initial_data()

# --- RUN ---
if __name__ == "__main__":
    app.run(debug=True)
