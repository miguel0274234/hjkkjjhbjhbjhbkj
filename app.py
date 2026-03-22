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

# --- CONFIGURAÇÃO ---
app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'portal_elim_v8.db')

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "elim-core-quantum-2026-v8-ultra"),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    MAX_CONTENT_LENGTH=100 * 1024 * 1024 # 100MB
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
CORS(app)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    progresso = db.relationship('ProgressoAula', backref='estudante', lazy='dynamic')

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
    data_conclusao = db.Column(db.DateTime)

class LogAtividade(db.Model):
    __tablename__ = "logs_atividades"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    acao = db.Column(db.String(255))
    ip_address = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- UTILITÁRIOS ---

def registrar_log(acao):
    if current_user.is_authenticated:
        log = LogAtividade(user_id=current_user.id, acao=acao, ip_address=request.remote_addr)
        db.session.add(log)
        db.session.commit()

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

# --- ROTAS DE LOGIN E REGISTRO ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        email = data.get('email')
        password = data.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_approved:
                return jsonify({"success": False, "error": "Conta aguardando aprovação."}), 401
            
            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            return jsonify({
                "success": True, 
                "redirect": url_for('dashboard'), 
                "role": user.role
            })
        
        return jsonify({"success": False, "error": "E-mail ou senha inválidos."}), 401

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    unidades = Unidade.query.all()
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        name, email, password = data.get('name'), data.get('email'), data.get('password')
        
        if User.query.filter_by(email=email).first():
            return jsonify({"success": False, "error": "E-mail já cadastrado."}), 400

        try:
            novo = User(name=name, email=email, unidade_id=data.get('unidade_id'), role="aluno")
            novo.set_password(password)
            db.session.add(novo)
            db.session.commit()
            return jsonify({"success": True, "message": "Cadastrado com sucesso!", "redirect": url_for('login')})
        except:
            return jsonify({"success": False, "error": "Erro no servidor."}), 500

    return render_template('register.html', unidades=unidades)

# --- ROTAS DO SISTEMA ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard")
@login_required
def dashboard():
    stats = {
        "aulas_count": Aula.query.count(),
        "meu_progresso": current_user.progresso.filter_by(concluido=True).count()
    }
    return render_template("home.html", **stats)

@app.route("/api/aulas/cadastrar", methods=['POST'])
@role_required('admin', 'professor')
def api_cadastrar_aula():
    data = request.get_json()
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
    try:
        concluidas = current_user.progresso.filter_by(concluido=True).all()
        total_aulas = Aula.query.filter_by(status="publicado").count()
        percentual = round((len(concluidas) / total_aulas * 100), 1) if total_aulas > 0 else 0
        ranking = User.query.order_by(User.xp.desc()).limit(5).all()
        
        return render_template("perfil.html", user=current_user, stats={
            "total_concluidas": len(concluidas),
            "percentual_total": percentual,
            "xp_falta_proximo_nivel": 1000 - (current_user.xp % 1000)
        }, ranking=ranking)
    except:
        return redirect(url_for('dashboard'))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- INICIALIZAÇÃO ---

def setup():
    with app.app_context():
        db.create_all()
        if not Unidade.query.first():
            db.session.add(Unidade(nome="Campus Central", cidade="Luanda"))
        if not User.query.filter_by(role="admin").first():
            admin = User(name="Admin", email="master@elim.edu", role="admin", is_approved=True, unidade_id=1, xp=2)
            admin.set_password("elim@2026")
            db.session.add(admin)
        db.session.commit()

if __name__ == "__main__":
    setup()
    app.run(debug=True, host="0.0.0.0", port=5000)
