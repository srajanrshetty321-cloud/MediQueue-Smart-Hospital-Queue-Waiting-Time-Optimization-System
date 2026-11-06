from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-before-deployment"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///mediqueue.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="patient")
    active = db.Column(db.Boolean, default=True)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    avg_minutes = db.Column(db.Integer, default=5)
    active = db.Column(db.Boolean, default=True)

class Counter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=False)
    available = db.Column(db.Boolean, default=True)
    department = db.relationship("Department")

class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(24), unique=True, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=False)
    counter_id = db.Column(db.Integer, db.ForeignKey("counter.id"))
    status = db.Column(db.String(20), default="Waiting")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    called_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    patient = db.relationship("User")
    department = db.relationship("Department")
    counter = db.relationship("Counter")

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class HospitalSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles: abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco

def log_action(action):
    db.session.add(AuditLog(actor_id=current_user.id if current_user.is_authenticated else None, action=action))
    db.session.commit()

def active_queue(dept_id):
    return Token.query.filter_by(department_id=dept_id, status="Waiting").order_by(Token.created_at.asc()).all()

def estimate(token):
    ahead = active_queue(token.department_id)
    try: position = next(i for i, t in enumerate(ahead, 1) if t.id == token.id)
    except StopIteration: position = 0
    dept = db.session.get(Department, token.department_id)
    counters = Counter.query.filter_by(department_id=token.department_id, available=True).count()
    counters = max(counters, 1)
    minutes = max(0, ((position - 1) * (dept.avg_minutes if dept else 5) + counters - 1) // counters)
    return position, minutes

@app.context_processor
def inject_globals():
    setting = HospitalSetting.query.filter_by(name="hospital_name").first()
    return {"hospital_name": setting.value if setting else "MediQueue"}

@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for({"admin":"admin_dashboard","staff":"staff_dashboard","patient":"patient_dashboard"}.get(current_user.role,"login")))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name, email, password = request.form.get("name","").strip(), request.form.get("email","").strip().lower(), request.form.get("password","")
        if not name or not email or len(password) < 6:
            flash("Enter a name, valid email and password of at least 6 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("That email is already registered.", "danger")
        else:
            user = User(name=name, email=email, role="patient"); user.set_password(password)
            db.session.add(user); db.session.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        user = User.query.filter_by(email=email, active=True).first()
        if user and user.check_password(request.form.get("password","")):
            login_user(user)
            return redirect(url_for({"admin":"admin_dashboard","staff":"staff_dashboard","patient":"patient_dashboard"}[user.role]))
        flash("Invalid email or password, or account is inactive.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user(); flash("You have been logged out.", "success"); return redirect(url_for("login"))

@app.route("/patient")
@role_required("patient")
def patient_dashboard():
    departments = Department.query.filter_by(active=True).order_by(Department.name).all()
    tokens = Token.query.filter_by(patient_id=current_user.id).order_by(Token.created_at.desc()).limit(20).all()
    active = Token.query.filter(Token.patient_id==current_user.id, Token.status.in_(["Waiting","Called","Serving"])).first()
    info = estimate(active) if active and active.status=="Waiting" else (0,0)
    return render_template("patient.html", departments=departments, tokens=tokens, active=active, info=info)

@app.route("/token/create", methods=["POST"])
@role_required("patient")
def create_token():
    dept = db.session.get(Department, request.form.get("department_id", type=int))
    if not dept or not dept.active:
        flash("Please select an available department.", "danger")
    elif Token.query.filter(Token.patient_id==current_user.id, Token.status.in_(["Waiting","Called","Serving"])).first():
        flash("You already have an active token. Complete or cancel it before requesting another.", "warning")
    else:
        count = Token.query.filter_by(department_id=dept.id).count() + 1
        token = Token(number=f"{dept.id:02d}-{date.today():%y%m%d}-{count:04d}", patient_id=current_user.id, department_id=dept.id)
        db.session.add(token); db.session.commit()
        flash(f"Token {token.number} generated successfully.", "success")
        socketio.emit("queue_update", {"message":"A new token joined the queue."})
    return redirect(url_for("patient_dashboard"))

@app.route("/token/<int:token_id>/cancel", methods=["POST"])
@role_required("patient")
def cancel_token(token_id):
    token = db.session.get(Token, token_id)
    if not token or token.patient_id != current_user.id: abort(404)
    if token.status == "Waiting":
        token.status = "Cancelled"; db.session.commit(); socketio.emit("queue_update", {"message":"Queue updated."}); flash("Token cancelled.", "success")
    else: flash("Only waiting tokens can be cancelled here.", "warning")
    return redirect(url_for("patient_dashboard"))

@app.route("/staff")
@role_required("staff","admin")
def staff_dashboard():
    departments = Department.query.filter_by(active=True).all()
    selected_id = request.args.get("department_id", type=int)
    if current_user.role == "staff" and not selected_id and departments: selected_id = departments[0].id
    dept = db.session.get(Department, selected_id) if selected_id else (departments[0] if departments else None)
    tokens = Token.query.filter_by(department_id=dept.id).filter(Token.status.in_(["Waiting","Called","Serving"])).order_by(Token.created_at.asc()).all() if dept else []
    counters = Counter.query.filter_by(department_id=dept.id).all() if dept else []
    return render_template("staff.html", departments=departments, dept=dept, tokens=tokens, counters=counters)

@app.route("/staff/call-next", methods=["POST"])
@role_required("staff","admin")
def call_next():
    dept_id = request.form.get("department_id", type=int)
    counter = db.session.get(Counter, request.form.get("counter_id", type=int))
    if not counter or counter.department_id != dept_id or not counter.available:
        flash("Choose an available counter for this department.", "danger")
    elif Token.query.filter_by(counter_id=counter.id, status="Serving").first():
        flash("This counter is already serving a patient.", "warning")
    else:
        token = Token.query.filter_by(department_id=dept_id, status="Waiting").order_by(Token.created_at.asc()).first()
        if token:
            token.status="Serving"; token.counter_id=counter.id; token.called_at=datetime.utcnow()
            db.session.add(AuditLog(actor_id=current_user.id, action=f"Called token {token.number} at {counter.name}"))
            db.session.commit(); flash(f"Now serving {token.number}.", "success")
            socketio.emit("queue_update", {"message":"A token has been called."})
        else: flash("No waiting patients in this department.", "info")
    return redirect(url_for("staff_dashboard", department_id=dept_id))

@app.route("/staff/token/<int:token_id>/<action>", methods=["POST"])
@role_required("staff","admin")
def token_action(token_id, action):
    token = db.session.get(Token, token_id)
    if not token: abort(404)
    if action == "complete" and token.status == "Serving":
        token.status="Completed"; token.completed_at=datetime.utcnow()
    elif action == "cancel" and token.status in ("Waiting","Called"):
        token.status="Cancelled"
    elif action == "noshow" and token.status in ("Serving","Called"):
        token.status="No-show"
    else:
        flash("That action is not valid for the current token status.", "warning")
        return redirect(url_for("staff_dashboard", department_id=token.department_id))
    db.session.add(AuditLog(actor_id=current_user.id, action=f"{action.title()} token {token.number}"))
    db.session.commit(); socketio.emit("queue_update", {"message":"Queue status changed."})
    flash(f"Token {token.number}: {token.status}.", "success")
    return redirect(url_for("staff_dashboard", department_id=token.department_id))

@app.route("/staff/counter/<int:counter_id>/toggle", methods=["POST"])
@role_required("staff","admin")
def toggle_counter(counter_id):
    counter = db.session.get(Counter, counter_id)
    if not counter: abort(404)
    if not counter.available and Token.query.filter_by(counter_id=counter.id, status="Serving").first():
        flash("Cannot disable a counter while it is serving a patient.", "warning")
    else:
        counter.available = not counter.available
        db.session.add(AuditLog(actor_id=current_user.id, action=f"Counter {counter.name} availability set to {counter.available}"))
        db.session.commit(); socketio.emit("queue_update", {"message":"Counter availability changed."})
        flash("Counter availability updated.", "success")
    return redirect(url_for("staff_dashboard", department_id=counter.department_id))

@app.route("/admin")
@role_required("admin")
def admin_dashboard():
    users = User.query.order_by(User.role, User.name).all()
    departments = Department.query.order_by(Department.name).all()
    counters = Counter.query.order_by(Counter.name).all()
    tokens = Token.query.order_by(Token.created_at.desc()).limit(30).all()
    total_today = Token.query.filter(db.func.date(Token.created_at)==date.today().isoformat()).count()
    waiting = Token.query.filter_by(status="Waiting").count()
    serving = Token.query.filter_by(status="Serving").count()
    completed = Token.query.filter_by(status="Completed").count()
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(15).all()
    stats = {"today":total_today,"waiting":waiting,"serving":serving,"completed":completed,"patients":User.query.filter_by(role="patient").count(),"staff":User.query.filter_by(role="staff").count()}
    return render_template("admin.html", users=users, departments=departments, counters=counters, tokens=tokens, stats=stats, logs=logs)

@app.route("/admin/user/<int:user_id>/edit", methods=["POST"])
@role_required("admin")
def edit_user(user_id):
    user = db.session.get(User,user_id)
    if not user: abort(404)
    name=request.form.get("name","").strip()
    if not name: flash("Name cannot be empty.","danger")
    else:
        old=user.name; user.name=name
        db.session.add(AuditLog(actor_id=current_user.id, action=f"Changed {user.role} name from '{old}' to '{name}'"))
        db.session.commit(); flash("Name updated and audit log recorded.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/department", methods=["POST"])
@role_required("admin")
def add_department():
    name=request.form.get("name","").strip()
    if not name: flash("Department name is required.","danger")
    elif Department.query.filter_by(name=name).first(): flash("Department already exists.","warning")
    else:
        db.session.add(Department(name=name, avg_minutes=max(1,request.form.get("avg_minutes",5,type=int))))
        db.session.commit(); flash("Department added.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/department/<int:dept_id>/edit", methods=["POST"])
@role_required("admin")
def edit_department(dept_id):
    dept=db.session.get(Department,dept_id)
    if not dept: abort(404)
    name=request.form.get("name","").strip()
    if not name: flash("Name is required.","danger")
    else:
        old=dept.name; dept.name=name; dept.avg_minutes=max(1,request.form.get("avg_minutes",dept.avg_minutes,type=int))
        db.session.add(AuditLog(actor_id=current_user.id,action=f"Renamed department '{old}' to '{name}'"))
        db.session.commit(); flash("Department updated.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/counter", methods=["POST"])
@role_required("admin")
def add_counter():
    name=request.form.get("name","").strip(); dept_id=request.form.get("department_id",type=int)
    if not name or not db.session.get(Department,dept_id): flash("Enter a counter name and valid department.","danger")
    elif Counter.query.filter_by(name=name).first(): flash("Counter name already exists.","warning")
    else: db.session.add(Counter(name=name,department_id=dept_id)); db.session.commit(); flash("Counter added.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/counter/<int:counter_id>/edit", methods=["POST"])
@role_required("admin")
def edit_counter(counter_id):
    counter=db.session.get(Counter,counter_id)
    if not counter: abort(404)
    name=request.form.get("name","").strip()
    if not name: flash("Counter name is required.","danger")
    else:
        old=counter.name; counter.name=name
        db.session.add(AuditLog(actor_id=current_user.id,action=f"Renamed counter '{old}' to '{name}'"))
        db.session.commit(); flash("Counter updated.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/settings", methods=["POST"])
@role_required("admin")
def update_settings():
    value=request.form.get("hospital_name","").strip()
    if value:
        setting=HospitalSetting.query.filter_by(name="hospital_name").first()
        if not setting: setting=HospitalSetting(name="hospital_name",value=value); db.session.add(setting)
        else: setting.value=value
        db.session.add(AuditLog(actor_id=current_user.id,action=f"Updated hospital name to '{value}'"))
        db.session.commit(); flash("Hospital name updated.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/user", methods=["POST"])
@role_required("admin")
def add_user():
    name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower()
    role=request.form.get("role","staff")
    password=request.form.get("password","")
    if role not in ("staff","patient") or not name or not email or len(password)<6:
        flash("Enter valid details. Password must be at least 6 characters.","danger")
    elif User.query.filter_by(email=email).first(): flash("Email already exists.","warning")
    else:
        u=User(name=name,email=email,role=role); u.set_password(password); db.session.add(u); db.session.commit(); flash(f"{role.title()} account created.","success")
    return redirect(url_for("admin_dashboard"))

@app.route("/health")
def health(): return {"status":"ok","app":"MediQueue"}

@app.errorhandler(403)
def forbidden(e): return render_template("error.html", code=403, message="You do not have permission to view this page."),403
@app.errorhandler(404)
def not_found(e): return render_template("error.html", code=404, message="The page or record was not found."),404

def seed():
    db.create_all()
    if not User.query.filter_by(email="admin@mediqueue.local").first():
        admin=User(name="MediQueue Admin",email="admin@mediqueue.local",role="admin"); admin.set_password("Admin@123"); db.session.add(admin)
    if Department.query.count()==0:
        names=["General Medicine","Cardiology","Orthopaedics","Dermatology","Paediatrics","ENT"]
        for n in names: db.session.add(Department(name=n,avg_minutes=7))
        db.session.commit()
    if Counter.query.count()==0:
        for d in Department.query.all()[:4]:
            db.session.add(Counter(name=f"{d.name.split()[0]} Counter 1",department_id=d.id))
    if not HospitalSetting.query.filter_by(name="hospital_name").first():
        db.session.add(HospitalSetting(name="hospital_name",value="MediQueue Hospital"))
    if not User.query.filter_by(email="staff@mediqueue.local").first():
        staff=User(name="Demo Staff",email="staff@mediqueue.local",role="staff"); staff.set_password("Staff@123"); db.session.add(staff)
    if not User.query.filter_by(email="patient@mediqueue.local").first():
        patient=User(name="Demo Patient",email="patient@mediqueue.local",role="patient"); patient.set_password("Patient@123"); db.session.add(patient)
    db.session.commit()

with app.app_context(): seed()

if __name__ == "__main__": socketio.run(app, host="127.0.0.1", port=5000, debug=True)
