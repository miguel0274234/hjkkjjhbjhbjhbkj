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
    xp = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"))
    
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    logs = db.relationship('LogAtividade', backref='owner', cascade="all, delete-orphan")
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
    user_agent = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

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
                flash("Área restrita. Você não possui as permissões necessárias.", "danger")
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def extrair_id_youtube(url):
    if not url: return ""
    regex = r'(?:v=|\/|be\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(regex, url)
    return match.group(1) if match else url

# --- ROTAS DE AUTENTICAÇÃO ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        email = data.get('email')
        password = data.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_approved:
                return jsonify({"success": False, "error": "Conta aguardando aprovação."}), 401
            
            if not user.is_active:
                return jsonify({"success": False, "error": "Esta conta foi desativada."}), 401

            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()
            registrar_log("Login realizado")

            if request.is_json:
                return jsonify({"success": True, "redirect": url_for('dashboard'), "role": user.role})
            return redirect(url_for('dashboard'))
        
        return jsonify({"success": False, "error": "Credenciais inválidas."}), 401

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: 
        return redirect(url_for('dashboard'))
    
    unidades = Unidade.query.all()
    
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')
        unidade_id = data.get('unidade_id')

        if not name or not email or not password:
            return jsonify({"success": False, "error": "Campos obrigatórios ausentes."}), 400

        if User.query.filter_by(email=email).first():
            return jsonify({"success": False, "error": "E-mail já cadastrado."}), 400

        try:
            novo_usuario = User(name=name, email=email, unidade_id=unidade_id, role="aluno")
            novo_usuario.set_password(password)
            db.session.add(novo_usuario)
            db.session.commit()
            return jsonify({"success": True, "message": "Cadastrado! Aguarde aprovação.", "redirect": url_for('login')})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": "Erro no servidor."}), 500

    return render_template('register.html', unidades=unidades)

@app.route("/logout")
@login_required
def logout():
    registrar_log("Logoff realizado")
    logout_user()
    return redirect(url_for('login'))

# --- ROTAS DO SISTEMA ---

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
        return jsonify({"success": False, "message": "Título obrigatório"}), 400

    try:
        slug = f"{data.get('nome').lower().replace(' ', '-')}-{str(uuid.uuid4())[:5]}"
        nova_aula = Aula(
            titulo=data.get('nome'),
            slug=slug,
            descricao=data.get('descricao'),
            url_video=extrair_id_youtube(data.get('url_video')),
            categoria=data.get('categoria', 'Geral'),
            minutos_estimados=int(data.get('tempo_estimado', 0)),
            quiz_data=data.get('quiz'),
            criado_por=current_user.id
        )
        db.session.add(nova_aula)
        db.session.commit()
        registrar_log(f"Nova aula: {nova_aula.titulo}")
        return jsonify({"success": True, "redirect": url_for('lista_aulas')})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": "Erro ao salvar aula."}), 500

@app.route("/perfil")
@login_required
def perfil():
    try:
        concluidas = current_user.progresso.filter_by(concluido=True)
        total_aulas = Aula.query.filter_by(status="publicado").count()
        percentual = round((concluidas.count() / total_aulas * 100), 1) if total_aulas > 0 else 0
        ranking = User.query.filter_by(is_active=True).order_by(User.xp.desc()).limit(5).all()
        
        notas = [p.nota_quiz for p in concluidas.all() if p.nota_quiz is not None]
        media = round(sum(notas) / len(notas), 1) if notas else 0
        
        xp_atual = current_user.xp or 0
        xp_falta = 1000 - (xp_atual % 1000)

        return render_template("perfil.html", 
            user=current_user,
            stats={"total_concluidas": concluidas.count(), "percentual_total": percentual, "media_notas": media, "xp_falta_proximo_nivel": xp_falta},
            ranking=ranking,
            logs=LogAtividade.query.filter_by(user_id=current_user.id).order_by(LogAtividade.timestamp.desc()).limit(10).all()
        )
    except Exception as e:
        flash("Erro ao carregar perfil.", "danger")
        return redirect(url_for('dashboard'))

@app.route("/api/perfil/atualizar", methods=['POST'])
@login_required
def api_atualizar_perfil():
    data = request.get_json()
    try:
        current_user.name = data.get('name', current_user.name)
        if data.get('new_password'):
            current_user.set_password(data.get('new_password'))
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# --- ADMINISTRAÇÃO ---

@app.route("/admin/usuarios")
@role_required('admin')
def gerenciar_usuarios():
    users = User.query.all()
    return render_template("admin_users.html", users=users)

@app.route("/api/admin/usuario/<int:uid>/action", methods=['POST'])
@role_required('admin')
def api_user_action(uid):
    user = User.query.get_or_404(uid)
    data = request.get_json()
    action = data.get('action')
    
    if action == 'approve': user.is_approved = True
    elif action == 'toggle_active': user.is_active = not user.is_active
    elif action == 'delete' and user.role != 'admin': db.session.delete(user)
    
    db.session.commit()
    return jsonify({"success": True})

# --- INICIALIZAÇÃO ---

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
            print(">>> Admin Pronto: master@elim.edu / elim@2026")

if __name__ == "__main__":
    setup_initial_data()
    app.run(debug=True, host="0.0.0.0", port=5000)
