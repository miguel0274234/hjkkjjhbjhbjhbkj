import os
import uuid
import re
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS

# --- CONFIGURAÇÃO DE ALTA PERFORMANCE V8 ---
app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "elim-core-quantum-2026-v8-ultra"),
    SQLALCHEMY_DATABASE_URI="sqlite:///portal_elim_v8.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_AS_ASCII=False,
    MAX_CONTENT_LENGTH=100 * 1024 * 1024, # 100MB
    UPLOAD_FOLDER=UPLOAD_FOLDER
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Sessão expirada ou acesso restrito."
login_manager.login_message_category = "warning"
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
    
    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"))
    
    # Novos campos para evitar o erro 500 na rota /perfil
    xp = db.Column(db.Integer, default=0)
    last_login = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamentos
    logs = db.relationship('LogAtividade', backref='owner', cascade="all, delete-orphan", lazy='dynamic')
    progresso = db.relationship('ProgressoAula', backref='user', cascade="all, delete-orphan", lazy='dynamic')
    notificacoes = db.relationship('Notification', backref='user', cascade="all, delete-orphan", lazy='dynamic')

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
    user_agent = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    mensagem = db.Column(db.String(255))
    lida = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- UTILITÁRIOS ---

def registrar_log(acao):
    if current_user.is_authenticated:
        log = LogAtividade(
            user_id=current_user.id, 
            acao=acao, 
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        db.session.add(log)
        db.session.commit()

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                if request.is_json:
                    return jsonify({"success": False, "error": "Acesso Negado"}), 403
                flash("Área restrita.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def extrair_id_youtube(url):
    if not url: return ""
    regex = r'(?:v=|\/|be\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(regex, url)
    return match.group(1) if match else url

# --- ROTAS ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard")
@login_required
def dashboard():
    stats = {
        "aulas_count": Aula.query.count(),
        "meu_progresso": current_user.progresso.filter_by(concluido=True).count(),
        "atividades": LogAtividade.query.filter_by(user_id=current_user.id).order_by(LogAtividade.timestamp.desc()).limit(8).all()
    }
    return render_template("home.html", **stats)

@app.route("/aulas")
@login_required
def lista_aulas():
    categoria = request.args.get('cat')
    query = Aula.query.filter_by(status="publicado")
    if categoria:
        query = query.filter_by(categoria=categoria)
    aulas = query.order_by(Aula.data_criacao.desc()).all()
    return render_template("aulas_lista.html", aulas=aulas)

@app.route("/upload", methods=['GET'])
@role_required('admin', 'professor')
def upload():
    return render_template("upload.html")

@app.route("/api/aulas/cadastrar", methods=['POST'])
@role_required('admin', 'professor')
def api_cadastrar_aula():
    data = request.get_json()
    if not data or not data.get('nome'):
        return jsonify({"success": False, "message": "O título é obrigatório"}), 400
    try:
        base_slug = data.get('nome').lower().replace(" ", "-")
        slug = f"{base_slug}-{str(uuid.uuid4())[:5]}"
        video_id = extrair_id_youtube(data.get('url_video'))
        
        nova_aula = Aula(
            titulo=data.get('nome'), slug=slug, descricao=data.get('descricao'),
            url_video=video_id, categoria=data.get('categoria', 'Geral'),
            minutos_estimados=int(data.get('tempo_estimado', 0)),
            quiz_data=data.get('quiz'), criado_por=current_user.id
        )
        db.session.add(nova_aula)
        db.session.commit()
        registrar_log(f"Cadastrou aula: {nova_aula.titulo}")
        return jsonify({"success": True, "redirect": url_for('lista_aulas')})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        user = User.query.filter_by(email=data.get('email')).first()
        
        if user and user.check_password(data.get('password')):
            if not user.is_approved:
                msg = "Sua conta aguarda aprovação."
                return jsonify({"success": False, "error": msg}), 401 if request.is_json else flash(msg, "info")
            
            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()
            registrar_log("Login realizado")
            
            if request.is_json:
                return jsonify({"success": True, "role": user.role, "redirect": url_for('dashboard')})
            return redirect(url_for('dashboard'))
            
        msg_erro = "Credenciais inválidas."
        if request.is_json: return jsonify({"success": False, "error": msg_erro}), 401
        flash(msg_erro, "danger")

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    unidades = Unidade.query.all()
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        if User.query.filter_by(email=data.get('email')).first():
            return jsonify({"success": False, "error": "E-mail já cadastrado"}), 409
        
        try:
            novo = User(name=data.get('name'), email=data.get('email'), unidade_id=data.get('unidade_id'), role="aluno")
            novo.set_password(data.get('password'))
            db.session.add(novo)
            db.session.flush()
            db.session.add(LogAtividade(user_id=novo.id, acao="Auto-registro", ip_address=request.remote_addr))
            db.session.commit()
            return jsonify({"success": True, "redirect": url_for('login')})
        except Exception:
            db.session.rollback()
            return jsonify({"success": False, "error": "Erro interno"}), 500
    return render_template('register.html', unidades=unidades)

@app.route("/perfil")
@login_required
def perfil():
    try:
        concluidas_query = current_user.progresso.filter_by(concluido=True)
        total_concluidas = concluidas_query.count()
        total_aulas = Aula.query.filter_by(status="publicado").count()
        
        percentual = round((total_concluidas / total_aulas * 100), 1) if total_aulas > 0 else 0
        ranking = User.query.filter_by(is_active=True).order_by(User.xp.desc()).limit(5).all()
        
        notas = [p.nota_quiz for p in concluidas_query.all() if p.nota_quiz is not None]
        media_geral = round(sum(notas) / len(notas), 1) if notas else 0
        
        alertas = Notification.query.filter_by(user_id=current_user.id, lida=False).all()
        logs = LogAtividade.query.filter_by(user_id=current_user.id).order_by(LogAtividade.timestamp.desc()).limit(10).all()

        xp_atual = current_user.xp or 0
        xp_para_proximo = 1000 - (xp_atual % 1000)

        return render_template("perfil.html", user=current_user, ranking=ranking, notificacoes=alertas, logs=logs,
            stats={"total_concluidas": total_concluidas, "percentual_total": percentual, "media_notas": media_geral, "xp_falta_proximo_nivel": xp_para_proximo})
    except Exception as e:
        print(f"Erro perfil: {e}")
        return redirect(url_for('dashboard'))

@app.route("/logout")
@login_required
def logout():
    registrar_log("Logoff")
    logout_user()
    return redirect(url_for('login'))

def setup_initial_data():
    with app.app_context():
        db.create_all()
        if not Unidade.query.first():
            db.session.add(Unidade(nome="Campus Central", cidade="Luanda"))
            db.session.commit()
        if not User.query.filter_by(role="admin").first():
            admin = User(name="Gestor Quantum", email="master@elim.edu", role="admin", is_approved=True, unidade_id=1)
            admin.set_password("elim@2026")
            db.session.add(admin)
            db.session.commit()

if __name__ == "__main__":
    setup_initial_data()
    app.run(debug=True, host="0.0.0.0", port=5000)
