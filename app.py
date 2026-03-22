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

# --- CONFIGURAÇÃO NÚCLEO ---
app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'portal_elim_v8.db')

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "elim-core-quantum-2026-v8-ultra"),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    MAX_CONTENT_LENGTH=100 * 1024 * 1024
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
CORS(app)

# --- TRATAMENTO DE ERROS GLOBAL (ANTI-HTML BUG) ---
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": "Rota não encontrada"}), 404
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({"success": False, "error": "Erro interno no servidor"}), 500
    return "Erro Crítico Interno", 500

# --- DATABASE MODELS ---

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
    role = db.Column(db.String(20), default="aluno", nullable=False) 
    xp = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"))
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    
    progresso = db.relationship('ProgressoAula', backref='estudante', lazy='dynamic', cascade="all, delete-orphan")

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
    categoria = db.Column(db.String(100), index=True)
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
    nota_quiz = db.Column(db.Float, nullable=True)
    data_conclusao = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- UTILITÁRIOS ---

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                if request.is_json or request.headers.get('Accept') == 'application/json':
                    return jsonify({"success": False, "error": "Acesso Negado"}), 403
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def extrair_id_youtube(url):
    if not url: return ""
    regex = r'(?:v=|\/|be\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(regex, url)
    return match.group(1) if match else url

# --- ROTAS DE ACESSO ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        user = User.query.filter_by(email=data.get('email')).first()
        
        if user and user.check_password(data.get('password')):
            if not user.is_approved:
                return jsonify({"success": False, "error": "Conta pendente de aprovação."}), 401
            
            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            return jsonify({"success": True, "redirect": url_for('dashboard')})
        
        return jsonify({"success": False, "error": "Credenciais incorretas."}), 401

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    unidades = Unidade.query.all()
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        if User.query.filter_by(email=data.get('email')).first():
            return jsonify({"success": False, "error": "E-mail já existe."}), 400

        try:
            novo = User(name=data.get('name'), email=data.get('email'), 
                        unidade_id=data.get('unidade_id'), role="aluno")
            novo.set_password(data.get('password'))
            db.session.add(novo)
            db.session.commit()
            return jsonify({"success": True, "message": "Sucesso!", "redirect": url_for('login')})
        except:
            db.session.rollback()
            return jsonify({"success": False, "error": "Erro no cadastro."}), 500

    return render_template('register.html', unidades=unidades)

# --- ROTAS DO SISTEMA ---

@app.route("/dashboard")
@login_required
def dashboard():
    total_aulas = Aula.query.filter_by(status="publicado").count()
    concluidas = current_user.progresso.filter_by(concluido=True).count()
    return render_template("home.html", total_aulas=total_aulas, concluidas=concluidas)

@app.route("/aula/<slug>")
@login_required
def ver_aula(slug):
    aula = Aula.query.filter_by(slug=slug).first_or_404()
    progresso = current_user.progresso.filter_by(aula_id=aula.id).first()
    return render_template("aula_view.html", aula=aula, progresso=progresso)

@app.route("/api/aulas/cadastrar", methods=['POST'])
@role_required('admin', 'professor')
def api_cadastrar_aula():
    data = request.get_json()
    if not data or not data.get('nome'):
        return jsonify({"success": False, "error": "Dados incompletos"}), 400
    try:
        slug = f"{data.get('nome').lower().replace(' ', '-')}-{str(uuid.uuid4())[:5]}"
        nova_aula = Aula(
            titulo=data.get('nome'), slug=slug, descricao=data.get('descricao'),
            url_video=extrair_id_youtube(data.get('url_video')),
            categoria=data.get('categoria', 'Geral'),
            minutos_estimados=int(data.get('tempo_estimado', 0)),
            quiz_data=data.get('quiz'), criado_por=current_user.id
        )
        db.session.add(nova_aula)
        db.session.commit()
        return jsonify({"success": True, "redirect": url_for('dashboard')})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/perfil")
@login_required
def perfil():
    concluidas = current_user.progresso.filter_by(concluido=True).count()
    total = Aula.query.filter_by(status="publicado").count()
    percentual = round((concluidas / total * 100), 1) if total > 0 else 0
    ranking = User.query.order_by(User.xp.desc()).limit(5).all()
    
    return render_template("perfil.html", user=current_user, stats={
        "total_concluidas": concluidas,
        "percentual": percentual,
        "xp_falta": 1000 - (current_user.xp % 1000)
    }, ranking=ranking)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- BOOTSTRAP ---

def init_system():
    with app.app_context():
        db.create_all()
        if not Unidade.query.first():
            db.session.add(Unidade(nome="Campus Central", cidade="Luanda"))
        if not User.query.filter_by(email="master@elim.edu").first():
            admin = User(name="Admin", email="master@elim.edu", role="admin", is_approved=True, unidade_id=1, xp=0)
            admin.set_password("elim@2026")
            db.session.add(admin)
        db.session.commit()

if __name__ == "__main__":
    init_system()
    app.run(debug=True, host="0.0.0.0", port=5000)
