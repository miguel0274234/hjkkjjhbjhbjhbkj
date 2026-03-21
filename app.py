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
    """Converte links normais do YouTube em IDs para Embed."""
    if not url: return ""
    # Trata links como https://www.youtube.com/watch?v=XXXX ou https://youtu.be/XXXX
    regex = r'(?:v=|\/|be\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(regex, url)
    return match.group(1) if match else url

# --- ROTAS PRINCIPAIS ---

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

# --- SISTEMA DE AULAS & CADASTRO ---

@app.route("/aulas")
@login_required
def lista_aulas():
    categoria = request.args.get('cat')
    query = Aula.query.filter_by(status="publicado")
    if categoria:
        query = query.filter_by(categoria=categoria)
    aulas = query.order_by(Aula.data_criacao.desc()).all()
    return render_template("aulas_lista.html", aulas=aulas)

@app.route("/upload", methods=['GET']) # Corrigido digitação para 'upload'
@role_required('admin', 'professor')
def upload():
    return render_template("upload.html")

@app.route("/api/aulas/cadastrar", methods=['POST']) # Rota que seu JS está chamando
@role_required('admin', 'professor')
def api_cadastrar_aula():
    data = request.get_json()
    
    if not data or not data.get('nome'):
        return jsonify({"success": False, "message": "O título da aula é obrigatório"}), 400

    try:
        # Gerar slug único para a URL amigável
        base_slug = data.get('nome').lower().replace(" ", "-")
        slug = f"{base_slug}-{str(uuid.uuid4())[:5]}"
        
        # Extrair apenas o ID do vídeo para garantir o Embed
        video_id = extrair_id_youtube(data.get('url_video'))
        
        nova_aula = Aula(
            titulo=data.get('nome'),
            slug=slug,
            descricao=data.get('descricao'),
            url_video=video_id,
            categoria=data.get('categoria', 'Geral'),
            minutos_estimados=int(data.get('tempo_estimado', 0)),
            quiz_data=data.get('quiz'), # Salva a lista de objetos como JSON no SQLite
            criado_por=current_user.id
        )
        
        db.session.add(nova_aula)
        db.session.commit()
        
        registrar_log(f"Cadastrou aula: {nova_aula.titulo}")
        
        return jsonify({
            "success": True, 
            "message": "Aula e Quiz cadastrados com sucesso!", 
            "redirect": url_for('lista_aulas')
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Erro ao salvar aula: {e}")
        return jsonify({"success": False, "message": "Erro ao salvar no banco de dados."}), 500

# --- ADMINISTRAÇÃO & AUTH (Mantidos conforme seu original) ---

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
    
    if action == 'approve':
        user.is_approved = True
    elif action == 'toggle_active':
        user.is_active = not user.is_active
    elif action == 'delete' and user.role != 'admin':
        db.session.delete(user)
    
    db.session.commit()
    return jsonify({"success": True})

@app.route('/login', methods=['GET', 'POST'])
def login():
    # 1. Se já estiver logado, redireciona direto
    if current_user.is_authenticated:
        if request.is_json:
            return jsonify({
                "success": True, 
                "role": current_user.role, 
                "redirect": url_for('dashboard')
            })
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        # 2. Captura dados de JSON (AJAX) ou Formulário comum
        data = request.get_json() if request.is_json else request.form
        email = data.get('email')
        password = data.get('password')
        
        # 3. Busca o usuário
        user = User.query.filter_by(email=email).first()
        
        # 4. Verificação de Credenciais
        if user and user.check_password(password):
            
            # 5. Verificação de Status (Aprovação/Ativo)
            if not user.is_approved:
                msg = "Acesso negado: Sua conta ainda aguarda aprovação administrativa."
                return jsonify({"success": False, "error": msg}), 401 if request.is_json else flash(msg, "info")
            
            if hasattr(user, 'is_active') and not user.is_active:
                msg = "Esta conta foi desativada pelo administrador."
                return jsonify({"success": False, "error": msg}), 401 if request.is_json else flash(msg, "danger")

            # 6. Executa o Login Real
            login_user(user, remember=True)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            registrar_log(f"Login realizado via {'JSON' if request.is_json else 'Form'}")

            # 7. Resposta de Sucesso (Envia ROLE e REDIRECT para o seu JS)
            if request.is_json:
                return jsonify({
                    "success": True, 
                    "role": user.role,  # Crucial para o seu script 'Acesso Mestre'
                    "redirect": url_for('dashboard')
                })
            
            return redirect(url_for('dashboard'))
            
        # 8. Erro de Credenciais (E-mail ou Senha incorretos)
        msg_erro = "Credenciais inválidas. Verifique seu e-mail e chave de acesso."
        if request.is_json:
            return jsonify({"success": False, "error": msg_erro}), 401
        
        flash(msg_erro, "danger")

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Se já estiver logado, vai direto para o dashboard
    if current_user.is_authenticated: 
        return redirect(url_for('dashboard'))
    
    # Busca unidades para preencher o campo de seleção no formulário
    unidades = Unidade.query.all()
    
    if request.method == 'POST':
        # Suporta tanto JSON (AJAX) quanto formulário comum
        data = request.get_json() if request.is_json else request.form
        
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')
        unidade_id = data.get('unidade_id')

        # Validações básicas
        if not name or not email or not password:
            msg = "Preencha todos os campos obrigatórios."
            return jsonify({"success": False, "error": msg}) if request.is_json else flash(msg, "danger")

        # Verifica se o e-mail já existe
        if User.query.filter_by(email=email).first():
            msg = "Este e-mail já está cadastrado."
            return jsonify({"success": False, "error": msg}) if request.is_json else flash(msg, "danger")

        try:
            novo_usuario = User(
                name=name,
                email=email,
                unidade_id=unidade_id,
                role="aluno",      # Por padrão, todo registro é aluno
                is_approved=False, # Precisa de aprovação do Admin
                is_active=True
            )
            novo_usuario.set_password(password)
            
            db.session.add(novo_usuario)
            db.session.commit()
            
            # Log de sistema
            log = LogAtividade(
                user_id=novo_usuario.id, 
                acao="Auto-registro realizado (Aguardando Aprovação)", 
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()

            msg = "Cadastro realizado com sucesso! Aguarde a aprovação de um administrador para entrar."
            if request.is_json:
                return jsonify({"success": True, "message": msg, "redirect": url_for('login')})
            
            flash(msg, "success")
            return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()
            print(f"Erro no registro: {e}")
            msg = "Erro interno ao processar cadastro."
            return jsonify({"success": False, "error": msg}) if request.is_json else flash(msg, "danger")

    return render_template('register.html', unidades=unidades)
@app.route("/logout")
@login_required
def logout():
    registrar_log("Logoff realizado")
    logout_user()
    return redirect(url_for('login'))
@app.route("/perfil")
@login_required
def perfil():
    try:
        # 1. Estatísticas de Estudo (Otimizado)
        # Usamos .count() direto no banco para performance em vez de carregar todos os objetos
        concluidas_query = current_user.progresso.filter_by(concluido=True)
        total_concluidas = concluidas_query.count()
        
        total_aulas = Aula.query.filter_by(status="publicado").count()
        
        # Proteção contra divisão por zero
        percentual = 0
        if total_aulas > 0:
            percentual = round((total_concluidas / total_aulas * 100), 1)
        
        # 2. Ranking Simples (Top 5 alunos por XP)
        # Filtramos apenas usuários ativos para o ranking ser justo
        ranking = User.query.filter_by(is_active=True).order_by(User.xp.desc()).limit(5).all()
        
        # 3. Média de Notas (Sem bugs de NoneType)
        # Buscamos as notas ignorando valores nulos
        notas = [p.nota_quiz for p in concluidas_query.all() if p.nota_quiz is not None]
        media_geral = 0
        if notas:
            media_geral = round(sum(notas) / len(notas), 1)
        
        # 4. Notificações não lidas
        alertas = current_user.notificacoes.filter_by(lida=False)\
            .order_by(Notification.created_at.desc()).all()
        
        # 5. Logs de Atividade
        logs = LogAtividade.query.filter_by(user_id=current_user.id)\
            .order_by(LogAtividade.timestamp.desc()).limit(10).all()

        # 6. Cálculo de XP para próximo nível
        # Evita bugs se o XP for exatamente múltiplo de 1000
        xp_atual = current_user.xp or 0
        xp_para_proximo = 1000 - (xp_atual % 1000)
        if xp_para_proximo == 0: xp_para_proximo = 1000

        return render_template("perfil.html", 
            user=current_user,
            stats={
                "total_concluidas": total_concluidas,
                "percentual_total": percentual,
                "media_notas": media_geral,
                "xp_falta_proximo_nivel": xp_para_proximo
            },
            ranking=ranking,
            notificacoes=alertas,
            logs=logs
        )

    except Exception as e:
        # Log do erro no console para debug e redirecionamento seguro
        print(f"Erro na rota de perfil: {e}")
        flash("Erro ao carregar informações do perfil.", "danger")
        return redirect(url_for('dashboard'))
@app.route("/api/perfil/atualizar", methods=['POST'])
@login_required
def api_atualizar_perfil():
    data = request.get_json()
    try:
        current_user.name = data.get('name', current_user.name)
        
        # Se o usuário quiser trocar a senha
        if data.get('new_password'):
            current_user.set_password(data.get('new_password'))
            
        db.session.commit()
        registrar_log("Atualizou informações do perfil")
        return jsonify({"success": True, "message": "Perfil atualizado com sucesso!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
# --- CONFIGURAÇÃO INICIAL ---

def setup_initial_data():
    with app.app_context():
        db.create_all()
        if not Unidade.query.first():
            db.session.add(Unidade(nome="Campus Central", cidade="Luanda"))
            db.session.commit()
            
        if not User.query.filter_by(role="admin").first():
            admin = User(
                name="Gestor Quantum", 
                email="master@elim.edu", 
                role="admin", 
                is_approved=True, 
                unidade_id=1
            )
            admin.set_password("elim@2026")
            db.session.add(admin)
            db.session.commit()
            print(">>> Sistema V8 Pronto. Admin: master@elim.edu / elim@2026")

if __name__ == "__main__":
    setup_initial_data()
    app.run(debug=True, host="0.0.0.0", port=5000)