from flask import Flask, request, redirect, make_response, jsonify, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import jwt
import datetime
import os
import logging
import secrets
import hashlib

app = Flask(__name__)

# =========================
# CONFIG
# =========================

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
TOKEN_EXP_HOURS = int(os.getenv("TOKEN_EXP_HOURS", "8"))
if not JWT_SECRET or JWT_SECRET in {"CAMBIA_SECRET", "dev-jwt-secret"}:
    raise RuntimeError("JWT_SECRET must be configured with a strong secret")

COOKIE_NAME = os.getenv("COOKIE_NAME", "quantlab_token")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
COOKIE_HTTPONLY = os.getenv("COOKIE_HTTPONLY", "true").lower() == "true"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "Lax")

DB_HOST = os.getenv("DB_HOST", "postgres_auth")
DB_NAME = os.getenv("DB_NAME", "quantlab_auth")
DB_USER = os.getenv("DB_USER", "quantlab")
DB_PASSWORD = os.getenv("DB_PASSWORD")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOCK_TIME_MINUTES = int(os.getenv("LOCK_TIME_MINUTES", "15"))
ENABLE_AUDIT_LOG = os.getenv("ENABLE_AUDIT_LOG", "true").lower() == "true"

if not DB_PASSWORD:
    raise RuntimeError("DB_PASSWORD must be configured")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD must be configured")

logging.basicConfig(level=logging.INFO)


# =========================
# DATABASE
# =========================

def get_db():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def log_event(event):
    if ENABLE_AUDIT_LOG:
        logging.info(event)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(80) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(30) NOT NULL DEFAULT 'student',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        failed_attempts INT NOT NULL DEFAULT 0,
        locked_until TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login_at TIMESTAMP NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS invitations (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        username VARCHAR(80),
        role VARCHAR(30) NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        accepted_at TIMESTAMP NULL,
        invited_by INT REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_resets (
        id SERIAL PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id),
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used_at TIMESTAMP NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_assets (
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol VARCHAR(40) NOT NULL,
        asset_type VARCHAR(20) NOT NULL,
        label VARCHAR(120),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, symbol)
    );
    """)
    cur.execute("ALTER TABLE user_assets ADD COLUMN IF NOT EXISTS label VARCHAR(120)")

    cur.execute("SELECT * FROM users WHERE username=%s", (ADMIN_USER,))
    admin = cur.fetchone()

    if not admin:
        cur.execute("""
        INSERT INTO users
        (username, password_hash, role, active, failed_attempts)
        VALUES (%s, %s, %s, TRUE, 0)
        """, (
            ADMIN_USER,
            generate_password_hash(ADMIN_PASSWORD),
            "admin"
        ))
        log_event(f"ADMIN CREATED: {ADMIN_USER}")

    conn.commit()
    cur.close()
    conn.close()


with app.app_context():
    try:
        init_db()
        logging.info("DB inicializada correctamente")
    except Exception as e:
        logging.error(f"DB INIT ERROR: {e}")


# =========================
# JWT
# =========================

def create_token(user):
    payload = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_EXP_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token():
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# =========================
# HTML LOGIN — QuantLabs Neon Design
# =========================

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuantLab AI Capital | Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
    --bg:#04000f;
    --card:#0d0820;
    --border:#1e0f3a;
    --primary:#00ff99;
    --accent:#00e5ff;
    --magenta:#ff00cc;
    --fg:#ffffff;
    --muted:#7070a0;
}
*, *::before, *::after { box-sizing:border-box; }
html, body { min-height:100%; }
body {
    margin:0;
    min-height:100vh;
    font-family:'Syne', sans-serif;
    color:var(--fg);
    background:var(--bg);
    display:flex;
    align-items:center;
    justify-content:center;
    overflow:hidden;
}
body::before {
    content:'';
    position:fixed;
    inset:0;
    background-image:
      linear-gradient(rgba(0,255,153,.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,153,.03) 1px, transparent 1px);
    background-size:48px 48px;
}
.orb {
    position:fixed;
    border-radius:50%;
    filter:blur(90px);
    pointer-events:none;
}
.orb1 { width:420px; height:420px; background:rgba(0,255,153,.09); top:-120px; left:-100px; }
.orb2 { width:340px; height:340px; background:rgba(0,229,255,.08); right:-80px; bottom:-80px; }
.orb3 { width:240px; height:240px; background:rgba(255,0,204,.07); left:55%; top:42%; }
.shell {
    position:relative;
    z-index:1;
    width:min(430px, calc(100vw - 32px));
}
.brand {
    display:flex;
    align-items:center;
    gap:12px;
    margin-bottom:18px;
}
.logo-icon {
    width:42px;
    height:42px;
    border-radius:11px;
    display:grid;
    place-items:center;
    background:linear-gradient(135deg, var(--primary), var(--accent));
    color:var(--bg);
    font:700 12px 'Space Mono', monospace;
}
.brand-copy strong {
    display:block;
    font-size:16px;
    line-height:1.1;
}
.brand-copy span {
    color:var(--muted);
    font-size:12px;
}
.card {
    background:rgba(13,8,32,.88);
    border:1px solid rgba(0,255,153,.14);
    border-radius:22px;
    padding:30px;
    box-shadow:0 24px 80px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.03);
    backdrop-filter:blur(18px);
}
.badge {
    display:inline-flex;
    align-items:center;
    gap:8px;
    border:1px solid rgba(0,255,153,.22);
    background:rgba(0,255,153,.07);
    color:var(--primary);
    border-radius:999px;
    padding:5px 10px;
    font:700 10px 'Space Mono', monospace;
    letter-spacing:.08em;
    text-transform:uppercase;
}
.badge::before {
    content:'';
    width:7px;
    height:7px;
    border-radius:50%;
    background:var(--primary);
    box-shadow:0 0 14px rgba(0,255,153,.7);
}
h1 {
    margin:18px 0 8px;
    font-size:28px;
    line-height:1.05;
    letter-spacing:-.04em;
}
h1 span { color:var(--primary); }
.lead {
    margin:0 0 22px;
    color:var(--muted);
    font-size:14px;
    line-height:1.5;
}
label {
    display:block;
    margin:14px 0 7px;
    color:rgba(255,255,255,.72);
    font:700 11px 'Space Mono', monospace;
    letter-spacing:.05em;
    text-transform:uppercase;
}
input {
    width:100%;
    border:1px solid rgba(255,255,255,.08);
    border-radius:12px;
    background:rgba(255,255,255,.04);
    color:var(--fg);
    padding:14px 15px;
    font:14px 'Syne', sans-serif;
    outline:none;
    transition:border-color .2s, box-shadow .2s, background .2s;
}
input::placeholder { color:rgba(255,255,255,.28); }
input:focus {
    border-color:rgba(0,229,255,.55);
    box-shadow:0 0 0 4px rgba(0,229,255,.08);
    background:rgba(255,255,255,.06);
}
button {
    width:100%;
    margin-top:20px;
    border:0;
    border-radius:12px;
    padding:14px 16px;
    cursor:pointer;
    color:#04000f;
    background:linear-gradient(135deg, var(--primary), var(--accent));
    font:800 14px 'Syne', sans-serif;
    transition:transform .2s, filter .2s, box-shadow .2s;
    box-shadow:0 12px 28px rgba(0,255,153,.16);
}
button:hover {
    transform:translateY(-1px);
    filter:saturate(1.15);
    box-shadow:0 16px 34px rgba(0,255,153,.22);
}
.error {
    margin-top:14px;
    padding:11px 12px;
    border-radius:12px;
    border:1px solid rgba(255,68,102,.28);
    background:rgba(255,68,102,.08);
    color:#ff8ca4;
    font-size:13px;
}
.footer {
    display:flex;
    justify-content:space-between;
    gap:12px;
    margin-top:18px;
    color:var(--muted);
    font:11px 'Space Mono', monospace;
}
@media (max-width:480px) {
    .card { padding:24px; }
    h1 { font-size:24px; }
    .footer { flex-direction:column; }
}
</style>
</head>
<body>
<div class="orb orb1"></div>
<div class="orb orb2"></div>
<div class="orb orb3"></div>
<div class="shell">
    <div class="brand">
        <div class="logo-icon">QL</div>
        <div class="brand-copy">
            <strong>QuantLabs AI</strong>
            <span>Capital dashboard</span>
        </div>
    </div>
    <div class="card">
        <div class="badge">Acceso protegido</div>
        <h1>Entrar al <span>dashboard</span></h1>
        <p class="lead">Acceso institucional con JWT, roles y sesión segura.</p>
        <form method="POST">
            <label for="username">Usuario</label>
            <input id="username" name="username" autocomplete="username" placeholder="Usuario" required>
            <label for="password">Contraseña</label>
            <input id="password" name="password" type="password" autocomplete="current-password" placeholder="Contraseña" required>
            <button type="submit">Entrar</button>
        </form>
        <div style="margin-top:14px;text-align:right;"><a href="/forgot-password" style="color:var(--accent);font-size:12px;">¿Olvidaste tu contraseña?</a></div>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <div class="footer">
            <span>Security Level 2</span>
            <span>JWT · PostgreSQL · Roles</span>
        </div>
    </div>
</div>
</body>
</html>
"""



STOCK_ASSETS = ["WMT","AAPL","PLTR","MSFT","NVDA","GOOGL","AMZN","META","TSM","BRK.B","V","JPM","XOM","LLY","MRK","UNH","PG","MA","CVX","KO","PEP","COST","TMO","ORCL","CSCO","NKE","VZ","ASML","TXN","ABT","TM","SAP","AMD","NFLX","NOW","ADBE","LVMUY","BABA","SHEL","TMUS","QCOM","PFE","SNY","AZN","TOT","GSK","RIO","BHP","MCD","HWM","WM","ADMA","ALMU","AVGO","ASTS","BE","DAVE","POWL"]
CRYPTO_ASSETS = ["BTC-USD","ETH-USD","USDT-USD","XRP-USD","LTC-USD","ADA-USD","DOT-USD","BCH-USD","XLM-USD","LINK-USD"]
FX_ASSETS = ["EURUSD=X","MXN=X","USDMXN=X","CADMXN=X","JPYMXN=X","EURMXN=X","GBPMXN=X","GBPUSD=X","USDJPY=X","EURJPY=X","AUDUSD=X","CADUSD=X"]
DEFAULT_USER_ASSETS = ["HWM","PLTR","NVDA","V","AMZN","WM"]
ASSET_GROUPS = {"Acciones": STOCK_ASSETS, "Criptos": CRYPTO_ASSETS, "Divisas": FX_ASSETS}
ASSET_TYPES = {s: group for group, items in ASSET_GROUPS.items() for s in items}

def sorted_asset_catalog():
    return {group: sorted(items, key=str.casefold) for group, items in sorted(ASSET_GROUPS.items())}

def ensure_user_assets(cur, user_id):
    cur.execute("SELECT COUNT(*) AS n FROM user_assets WHERE user_id=%s", (user_id,))
    if cur.fetchone()["n"] == 0:
        cur.executemany("INSERT INTO user_assets(user_id,symbol,asset_type) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", [(user_id,s,ASSET_TYPES[s]) for s in DEFAULT_USER_ASSETS])

ACCOUNT_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mi cuenta | QuantLab</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>:root{--bg:#04000f;--card:#0d0820;--border:#1e0f3a;--primary:#00ff99;--accent:#00e5ff;--magenta:#ff00cc;--fg:#fff;--muted:#7070a0}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:'Syne',sans-serif}header{height:72px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 28px}.brand{font-weight:800}.brand em{color:var(--primary);font-style:normal}.layout{display:grid;grid-template-columns:240px 1fr;min-height:calc(100vh - 72px)}aside{border-right:1px solid var(--border);padding:24px 14px}.nav-label{font:700 10px 'Space Mono';color:var(--muted);text-transform:uppercase;margin:8px 10px}.nav-link{display:flex;gap:10px;color:var(--muted);text-decoration:none;padding:12px;border-radius:12px}.nav-link.active,.nav-link:hover{background:rgba(0,255,153,.08);color:var(--fg)}main{padding:34px}.page h1{font-size:32px;margin:0 0 8px}.page h1 span{color:var(--primary)}.sub{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.card{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:20px}.label{font:700 11px 'Space Mono';color:var(--muted);text-transform:uppercase}.value{font-size:24px;font-weight:800;margin-top:8px}.details{display:grid;grid-template-columns:1fr 1fr;gap:16px}.row{display:flex;justify-content:space-between;padding:14px 0;border-bottom:1px solid rgba(255,255,255,.06)}.pill{display:inline-block;border:1px solid rgba(0,255,153,.25);background:rgba(0,255,153,.08);color:var(--primary);border-radius:999px;padding:5px 10px;font:700 11px 'Space Mono'}.assets{margin-top:16px}.asset-group{margin-top:16px}.asset-group h4{color:var(--accent);margin:0 0 10px}.chips{display:flex;flex-wrap:wrap;gap:9px}.chip{position:relative}.chip input{position:absolute;opacity:0}.chip span{display:block;padding:9px 12px;border:1px solid var(--border);border-radius:999px;color:var(--muted);cursor:pointer;background:#090514}.chip input:checked+span{color:var(--primary);border-color:rgba(0,255,153,.35);background:rgba(0,255,153,.08)}button.save,button.mini{border:1px solid rgba(0,255,153,.3);background:rgba(0,255,153,.1);color:var(--primary);border-radius:10px;padding:10px 13px;font-weight:700;cursor:pointer}.msg{margin-left:12px;color:var(--accent)}.asset-form{display:grid;grid-template-columns:1fr 160px 1fr auto;gap:10px;margin:18px 0}.asset-form input,.asset-form select,.asset-row input,.asset-row select{border:1px solid var(--border);background:#090514;color:var(--fg);border-radius:10px;padding:11px}.asset-list{display:grid;gap:10px}.asset-row{display:grid;grid-template-columns:1fr 150px 1fr auto auto;gap:10px;align-items:center}.usage-card{margin-top:16px}.usage-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}.usage-kpi{padding:14px;border:1px solid rgba(255,255,255,.06);border-radius:14px;background:#090514}.usage-kpi strong{display:block;font-size:24px;margin-top:7px}.usage-kpi small{color:var(--muted);font:700 10px 'Space Mono';text-transform:uppercase}.usage-table{width:100%;border-collapse:collapse;margin-top:14px}.usage-table th,.usage-table td{padding:10px 0;border-bottom:1px solid rgba(255,255,255,.06);text-align:left}.usage-table th{color:var(--muted);font:700 10px 'Space Mono';text-transform:uppercase}.usage-muted{color:var(--muted)}.asset-list{display:grid;gap:10px}.asset-row{display:grid;grid-template-columns:1fr 150px 1fr auto auto;gap:10px;align-items:center}.danger{color:#ff4466!important;border-color:rgba(255,68,102,.3)!important;background:rgba(255,68,102,.08)!important}.catalog-note{margin-top:18px}@media(max-width:800px){.layout{grid-template-columns:1fr}aside{display:none}.grid,.details{grid-template-columns:1fr}main{padding:22px}}</style></head>
<body><header><div class="brand">QuantLab <em>AI Capital</em></div><a href="/dashboard/" class="pill">← Dashboard</a></header><div class="layout"><aside><div class="nav-label">Plataforma</div><a href="/dashboard/" class="nav-link">⬡ Dashboard</a><a href="/auth/me" class="nav-link active">👤 Mi cuenta</a><a href="/logout" class="nav-link">⏻ Logout</a></aside><main><div class="page"><h1>Mi <span>cuenta</span></h1><div class="sub">Información de acceso, seguridad y consumo de tu usuario.</div></div><div class="grid"><div class="card"><div class="label">Usuario</div><div class="value">{{ user.username }}</div></div><div class="card"><div class="label">Rol</div><div class="value">{{ user.role }}</div></div><div class="card"><div class="label">Estado</div><div class="value">{{ 'Activo' if user.active else 'Inactivo' }}</div></div></div><div class="details"><div class="card"><div class="label">Seguridad</div><div class="row"><span>Intentos fallidos</span><b>{{ user.failed_attempts }}</b></div><div class="row"><span>Bloqueado hasta</span><b>{{ user.locked_until or '—' }}</b></div></div><div class="card"><div class="label">Historial</div><div class="row"><span>Creada</span><b>{{ user.created_at }}</b></div><div class="row"><span>Último login</span><b>{{ user.last_login_at or '—' }}</b></div></div></div><div class="card usage-card"><div class="label">Consumo de tokens</div><p class="sub">Uso agregado del Harness para este usuario en todas sus conversaciones.</p><div id="tokenUsageProfile" class="usage-grid"><div class="usage-kpi"><small>Total</small><strong>—</strong></div><div class="usage-kpi"><small>Prompt</small><strong>—</strong></div><div class="usage-kpi"><small>Respuesta</small><strong>—</strong></div><div class="usage-kpi"><small>Sesiones</small><strong>—</strong></div></div><div id="tokenUsageDetail" class="usage-muted" style="margin-top:12px">Cargando consumo…</div></div>{% if user.role == 'admin' %}<div class="card usage-card"><div class="label">Administración</div><p class="sub">Sólo el usuario admin puede crear invitaciones, activar/desactivar usuarios y cambiar roles.</p><a class="pill" href="/admin/users">Administrar usuarios</a></div>{% endif %}</main></div><script src="/dashboard/js/platform-menu.js?v=20260523-shared"></script><script>
function fmtNum(v){return Number(v||0).toLocaleString('es-MX')}
async function loadTokenUsage(){
  const box=document.getElementById('tokenUsageProfile'), detail=document.getElementById('tokenUsageDetail');
  try{
    const r=await fetch('/harness-api/v1/user/usage',{credentials:'same-origin',cache:'no-store'});
    if(!r.ok) throw new Error('No disponible');
    const d=await r.json(), usage=d.usage||{}, totals=usage.totals||{}, sessions=usage.sessions||[];
    const total=(totals.total_tokens||0)+(totals.estimated_tokens||0);
    box.innerHTML=`<div class="usage-kpi"><small>Total</small><strong>${fmtNum(total)}</strong></div><div class="usage-kpi"><small>Prompt</small><strong>${fmtNum(totals.prompt_tokens)}</strong></div><div class="usage-kpi"><small>Respuesta</small><strong>${fmtNum(totals.completion_tokens)}</strong></div><div class="usage-kpi"><small>Sesiones</small><strong>${fmtNum(totals.sessions)}</strong></div>`;
    const rows=sessions.slice(0,6).map(s=>`<tr><td>${s.title||s.id}</td><td>${fmtNum((s.total_tokens||0)+(s.estimated_tokens||0))}</td><td>${fmtNum(s.messages)}</td><td>${s.updated_at||'—'}</td></tr>`).join('');
    detail.innerHTML=`<div>Mensajes: <b>${fmtNum(totals.messages)}</b> · Tareas: <b>${fmtNum(totals.tasks)}</b> · Última actividad: <b>${totals.last_activity_at||'—'}</b></div>${rows?`<table class="usage-table"><thead><tr><th>Conversación</th><th>Tokens</th><th>Mensajes</th><th>Actualizada</th></tr></thead><tbody>${rows}</tbody></table>`:''}`;
  }catch(e){
    detail.textContent='No fue posible cargar el consumo de tokens del Harness.';
  }
}
loadTokenUsage();
</script></body></html>
"""

# =========================
# ROUTES
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
        SELECT * FROM users
        WHERE username=%s AND active=TRUE
        """, (username,))

        user = cur.fetchone()

        if not user:
            log_event(f"LOGIN FAIL USER NOT FOUND: {username}")
            cur.close()
            conn.close()
            return render_template_string(LOGIN_HTML, error="Usuario o contraseña incorrectos")

        now = datetime.datetime.utcnow()

        if user["locked_until"] and user["locked_until"] > now:
            log_event(f"LOGIN BLOCKED: {username}")
            cur.close()
            conn.close()
            return render_template_string(LOGIN_HTML, error="Cuenta bloqueada temporalmente. Intenta en {} minutos.".format(LOCK_TIME_MINUTES))

        if check_password_hash(user["password_hash"], password):
            cur.execute("""
            UPDATE users
            SET failed_attempts = 0, locked_until = NULL, last_login_at = NOW()
            WHERE id=%s
            """, (user["id"],))
            conn.commit()

            token = create_token(user)

            resp = make_response(redirect("/dashboard/"))
            resp.set_cookie(
                COOKIE_NAME,
                token,
                httponly=COOKIE_HTTPONLY,
                secure=COOKIE_SECURE,
                samesite=COOKIE_SAMESITE,
                max_age=TOKEN_EXP_HOURS * 60 * 60,
                path="/"
            )

            log_event(f"LOGIN SUCCESS: {username}")
            cur.close()
            conn.close()
            return resp

        new_attempts = user["failed_attempts"] + 1

        if new_attempts >= MAX_LOGIN_ATTEMPTS:
            cur.execute("""
            UPDATE users
            SET failed_attempts=%s,
                locked_until = NOW() + (%s || ' minutes')::interval
            WHERE id=%s
            """, (new_attempts, LOCK_TIME_MINUTES, user["id"]))
            log_event(f"USER LOCKED: {username}")
            error = f"Demasiados intentos fallidos. Cuenta bloqueada por {LOCK_TIME_MINUTES} minutos."
        else:
            cur.execute("""
            UPDATE users SET failed_attempts=%s WHERE id=%s
            """, (new_attempts, user["id"]))
            remaining = MAX_LOGIN_ATTEMPTS - new_attempts
            error = f"Contraseña incorrecta. Te quedan {remaining} intento(s)."

        conn.commit()
        cur.close()
        conn.close()
        log_event(f"LOGIN FAIL: {username}")

    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    resp = make_response(redirect("/"))
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.route("/auth/verify")
def verify():
    data = decode_token()
    if not data:
        return "Unauthorized", 401
    return "OK", 200


@app.route("/auth/userinfo")
def userinfo():
    data = decode_token()
    if not data:
        return {"error": "unauthorized"}, 401
    return {"user_id": data["user_id"], "username": data["username"], "role": data["role"]}


@app.route("/auth/me")
def me():
    data = decode_token()
    if not data:
        return redirect("/login")
    conn=get_db(); cur=conn.cursor()
    ensure_user_assets(cur, data["user_id"])
    conn.commit()
    cur.execute("""SELECT username, role, active, failed_attempts, locked_until, created_at, last_login_at FROM users WHERE id=%s""", (data["user_id"],))
    user=cur.fetchone(); cur.close(); conn.close()
    if not user:
        return redirect("/login")
    return render_template_string(ACCOUNT_HTML, user=user)



@app.route("/auth/assets", methods=["GET", "PUT", "POST"])
def user_assets():
    data=decode_token()
    if not data: return jsonify({"error":"No autenticado"}), 401
    conn=get_db(); cur=conn.cursor(); ensure_user_assets(cur, data["user_id"]); conn.commit()
    if request.method == "PUT":
        body=request.json or {}; symbols=[]
        for sym in body.get("symbols", []):
            sym=(sym or "").strip().upper()
            if sym and sym not in symbols: symbols.append(sym)
        if not symbols: cur.close(); conn.close(); return jsonify({"error":"Selecciona al menos un activo"}), 400
        cur.execute("DELETE FROM user_assets WHERE user_id=%s", (data["user_id"],))
        cur.executemany("INSERT INTO user_assets(user_id,symbol,asset_type,label) VALUES(%s,%s,%s,%s)", [(data["user_id"],s,ASSET_TYPES.get(s,'Otros'),s) for s in symbols])
        conn.commit()
    elif request.method == "POST":
        body=request.json or {}; symbol=(body.get("symbol") or "").strip().upper(); typ=(body.get("asset_type") or ASSET_TYPES.get(symbol) or "Otros").strip(); label=(body.get("label") or symbol).strip()
        if not symbol or len(symbol)>40: cur.close(); conn.close(); return jsonify({"error":"Ticker inválido"}), 400
        if typ not in ["Acciones","Criptos","Divisas","Otros"]: cur.close(); conn.close(); return jsonify({"error":"Tipo inválido"}), 400
        try:
            cur.execute("INSERT INTO user_assets(user_id,symbol,asset_type,label) VALUES(%s,%s,%s,%s)", (data["user_id"],symbol,typ,label)); conn.commit()
        except Exception:
            conn.rollback(); cur.close(); conn.close(); return jsonify({"error":"El ticker ya existe"}), 409
    cur.execute("SELECT symbol,asset_type,label FROM user_assets WHERE user_id=%s ORDER BY asset_type,symbol", (data["user_id"],))
    assets=cur.fetchall(); selected=[r["symbol"] for r in assets]; cur.close(); conn.close()
    return jsonify({"catalog":sorted_asset_catalog(), "selected":selected, "assets":assets})

@app.route("/auth/assets/<path:symbol>", methods=["PATCH", "DELETE"])
def mutate_user_asset(symbol):
    data=decode_token()
    if not data: return jsonify({"error":"No autenticado"}), 401
    symbol=symbol.strip().upper(); conn=get_db(); cur=conn.cursor()
    if request.method == "DELETE":
        cur.execute("SELECT COUNT(*) AS n FROM user_assets WHERE user_id=%s", (data["user_id"],)); n=cur.fetchone()["n"]
        if n <= 1: cur.close(); conn.close(); return jsonify({"error":"Debes conservar al menos un activo"}), 400
        cur.execute("DELETE FROM user_assets WHERE user_id=%s AND symbol=%s", (data["user_id"],symbol)); conn.commit(); cur.close(); conn.close(); return jsonify({"message":"Activo eliminado"})
    body=request.json or {}; new=(body.get("symbol") or symbol).strip().upper(); typ=(body.get("asset_type") or "Otros").strip(); label=(body.get("label") or new).strip()
    if not new or len(new)>40 or typ not in ["Acciones","Criptos","Divisas","Otros"]: cur.close(); conn.close(); return jsonify({"error":"Datos inválidos"}), 400
    try:
        cur.execute("UPDATE user_assets SET symbol=%s,asset_type=%s,label=%s WHERE user_id=%s AND symbol=%s", (new,typ,label,data["user_id"],symbol)); conn.commit()
    except Exception:
        conn.rollback(); cur.close(); conn.close(); return jsonify({"error":"No se pudo actualizar; quizá el ticker ya existe"}), 409
    cur.close(); conn.close(); return jsonify({"message":"Activo actualizado"})


@app.route("/auth/register", methods=["POST"])
def register():
    data = decode_token()
    if not data or data["role"] != "admin":
        return jsonify({"error": "Solo admin puede crear usuarios"}), 403

    body = request.json or {}
    username = body.get("username")
    password = body.get("password")
    role = body.get("role", "viewer")

    if not username or not password:
        return jsonify({"error": "username y password son obligatorios"}), 400

    if role not in ["admin", "teacher", "student", "trader"]:
        return jsonify({"error": "Rol inválido"}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
        INSERT INTO users
        (username, password_hash, role, active, failed_attempts)
        VALUES (%s, %s, %s, TRUE, 0)
        """, (username, generate_password_hash(password), role))
        conn.commit()
        log_event(f"USER CREATED: {username} ROLE {role}")
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({"error": str(e)}), 400

    cur.close()
    conn.close()
    return jsonify({"message": "Usuario creado", "username": username, "role": role})


@app.route("/auth/users")
def users():
    data = decode_token()
    if not data or data["role"] != "admin":
        return jsonify({"error": "Solo admin"}), 403

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    SELECT id, username, role, active, failed_attempts, locked_until, created_at
    FROM users ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(rows)



def token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()

def require_admin():
    data = decode_token()
    return data if data and data.get("role") == "admin" else None

ADMIN_USERS_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Usuarios | QuantLab</title><script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<style>body{margin:0;background:#04000f;color:#fff;font-family:Arial}.wrap{max-width:1080px;margin:40px auto;padding:20px}.card{background:#0d0820;border:1px solid #1e0f3a;border-radius:16px;padding:20px;margin-bottom:18px}input,select,button{padding:11px;border-radius:10px;border:1px solid #2b1a48;background:#090514;color:#fff}button{background:#00ff99;color:#04000f;font-weight:700;cursor:pointer}.grid{display:grid;grid-template-columns:1fr 1fr 160px auto;gap:10px}.row{display:grid;grid-template-columns:1fr 140px 100px 150px 150px;gap:10px;padding:10px 0;border-top:1px solid #24153d;align-items:center}.muted{color:#9d96b8}.link{word-break:break-all;color:#00e5ff}.mini{padding:8px}.danger{background:#ff4466;color:#fff}</style></head>
<body><div id="app" class="wrap"><p><a class="link" href="/dashboard/">← Dashboard</a></p><h1>Administración de usuarios</h1><p class="muted">Acceso exclusivo para rol admin.</p><div class="card"><h3>Crear invitación</h3><div class="grid"><input v-model="email" placeholder="email"><input v-model="username" placeholder="usuario sugerido"><select v-model="role"><option>student</option><option>teacher</option><option>trader</option><option>admin</option></select><button @click="invite">Invitar</button></div><p v-if="inviteUrl" class="link">{{inviteUrl}}</p></div><div class="card"><h3>Usuarios</h3><div class="row muted"><span>Usuario</span><span>Rol</span><span>Activo</span><span>Cambiar rol</span><span>Acción</span></div><div class="row" v-for="u in users"><span>{{u.username}}</span><span>{{u.role}}</span><span>{{u.active}}</span><select v-model="u.role" class="mini"><option>student</option><option>teacher</option><option>trader</option><option>admin</option></select><div><button class="mini" @click="saveRole(u)">Guardar</button> <button class="mini" :class="{danger:u.active}" @click="toggle(u)">{{u.active?'Desactivar':'Activar'}}</button></div></div></div></div>
<script>Vue.createApp({data(){return{users:[],email:'',username:'',role:'student',inviteUrl:''}},mounted(){this.load()},methods:{async load(){this.users=await (await fetch('/auth/users')).json()},async invite(){let r=await fetch('/auth/invitations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:this.email,username:this.username,role:this.role})});let d=await r.json();this.inviteUrl=d.invite_url||d.error},async saveRole(u){await fetch('/auth/users/'+u.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({role:u.role})});this.load()},async toggle(u){await fetch('/auth/users/'+u.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:!u.active})});this.load()}}}).mount('#app')</script></body></html>
"""

ACTIVATE_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Activar cuenta</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#04000f;color:#fff;font-family:Arial}.card{width:min(420px,90vw);background:#0d0820;border:1px solid #1e0f3a;border-radius:18px;padding:28px}input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:10px}input{background:#090514;color:#fff;border:1px solid #2b1a48}button{border:0;background:#00ff99;font-weight:700}.msg{margin-top:12px;color:#00e5ff}</style></head><body><div class="card"><h1>Activa tu cuenta</h1><form method="post"><input name="username" placeholder="Usuario" value="{{ username or '' }}" required><input name="password" type="password" placeholder="Contraseña" minlength="10" required><button>Activar cuenta</button></form>{% if message %}<div class="msg">{{ message }}</div>{% endif %}</div></body></html>
"""

@app.route("/admin/users")
def admin_users_page():
    if not require_admin():
        return "Forbidden", 403
    return ADMIN_USERS_HTML

@app.route("/auth/invitations", methods=["POST"])
def create_invitation():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Solo admin"}), 403
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    username = (body.get("username") or "").strip() or None
    role = body.get("role", "student")
    if not email or role not in ["admin", "teacher", "student", "trader"]:
        return jsonify({"error": "email o rol inválido"}), 400
    raw = secrets.token_urlsafe(32)
    conn=get_db(); cur=conn.cursor()
    cur.execute("""INSERT INTO invitations(email, username, role, token_hash, expires_at, invited_by)
                   VALUES (%s,%s,%s,%s,NOW()+INTERVAL '7 days',%s)""", (email, username, role, token_hash(raw), admin["user_id"]))
    conn.commit(); cur.close(); conn.close()
    base = request.host_url.rstrip('/')
    return jsonify({"message":"Invitación creada", "invite_url": f"{base}/activate/{raw}", "role":role})

@app.route("/activate/<raw>", methods=["GET", "POST"])
def activate_invitation(raw):
    conn=get_db(); cur=conn.cursor()
    cur.execute("""SELECT * FROM invitations WHERE token_hash=%s AND accepted_at IS NULL AND expires_at>NOW()""", (token_hash(raw),))
    inv=cur.fetchone()
    if not inv:
        cur.close(); conn.close(); return render_template_string(ACTIVATE_HTML, username=None, message="Invitación inválida o vencida"), 400
    if request.method == "POST":
        username=(request.form.get("username") or "").strip()
        password=request.form.get("password") or ""
        if len(password) < 10:
            return render_template_string(ACTIVATE_HTML, username=username, message="La contraseña debe tener al menos 10 caracteres")
        try:
            cur.execute("""INSERT INTO users(username,password_hash,role,active,failed_attempts)
                           VALUES(%s,%s,%s,TRUE,0) RETURNING id""", (username, generate_password_hash(password), inv["role"]))
            cur.execute("UPDATE invitations SET accepted_at=NOW(), username=%s WHERE id=%s", (username, inv["id"]))
            conn.commit()
            return redirect("/login")
        except Exception as e:
            conn.rollback()
            return render_template_string(ACTIVATE_HTML, username=username, message="No se pudo activar: usuario ya existe")
        finally:
            cur.close(); conn.close()
    cur.close(); conn.close()
    return render_template_string(ACTIVATE_HTML, username=inv["username"], message=None)


RESET_REQUEST_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recuperar contraseña</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#04000f;color:#fff;font-family:Arial}.card{width:min(420px,90vw);background:#0d0820;border:1px solid #1e0f3a;border-radius:18px;padding:28px}input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:10px}input{background:#090514;color:#fff;border:1px solid #2b1a48}button{border:0;background:#00ff99;font-weight:700}.msg{margin-top:12px;color:#00e5ff}.link{word-break:break-all;color:#00e5ff}</style></head><body><div class="card"><h1>Recuperar contraseña</h1><form method="post"><input name="username" placeholder="Usuario" required><button>Generar enlace</button></form>{% if message %}<div class="msg">{{ message }}</div>{% endif %}{% if reset_url %}<div class="link">{{ reset_url }}</div>{% endif %}</div></body></html>
"""

RESET_HTML = """
<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nueva contraseña</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#04000f;color:#fff;font-family:Arial}.card{width:min(420px,90vw);background:#0d0820;border:1px solid #1e0f3a;border-radius:18px;padding:28px}input,button{width:100%;box-sizing:border-box;padding:13px;margin-top:10px;border-radius:10px}input{background:#090514;color:#fff;border:1px solid #2b1a48}button{border:0;background:#00ff99;font-weight:700}.msg{margin-top:12px;color:#00e5ff}</style></head><body><div class="card"><h1>Nueva contraseña</h1><form method="post"><input name="password" type="password" placeholder="Nueva contraseña" minlength="10" required><button>Actualizar</button></form>{% if message %}<div class="msg">{{ message }}</div>{% endif %}</div></body></html>
"""

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username=(request.form.get("username") or "").strip()
        conn=get_db(); cur=conn.cursor()
        cur.execute("SELECT id FROM users WHERE username=%s AND active=TRUE", (username,))
        user=cur.fetchone()
        reset_url=None
        if user:
            raw=secrets.token_urlsafe(32)
            cur.execute("""INSERT INTO password_resets(user_id,token_hash,expires_at)
                           VALUES(%s,%s,NOW()+INTERVAL '1 hour')""", (user["id"], token_hash(raw)))
            conn.commit()
            reset_url=f"{request.host_url.rstrip('/')}/reset-password/{raw}"
        cur.close(); conn.close()
        return render_template_string(RESET_REQUEST_HTML, message="Si el usuario existe, se generó un enlace de recuperación.", reset_url=reset_url)
    return render_template_string(RESET_REQUEST_HTML, message=None, reset_url=None)

@app.route("/reset-password/<raw>", methods=["GET", "POST"])
def reset_password(raw):
    conn=get_db(); cur=conn.cursor()
    cur.execute("""SELECT * FROM password_resets WHERE token_hash=%s AND used_at IS NULL AND expires_at>NOW()""", (token_hash(raw),))
    row=cur.fetchone()
    if not row:
        cur.close(); conn.close(); return render_template_string(RESET_HTML, message="Enlace inválido o vencido"), 400
    if request.method == "POST":
        password=request.form.get("password") or ""
        if len(password)<10:
            return render_template_string(RESET_HTML, message="La contraseña debe tener al menos 10 caracteres")
        cur.execute("UPDATE users SET password_hash=%s, failed_attempts=0, locked_until=NULL WHERE id=%s", (generate_password_hash(password), row["user_id"]))
        cur.execute("UPDATE password_resets SET used_at=NOW() WHERE id=%s", (row["id"],))
        conn.commit(); cur.close(); conn.close()
        return redirect("/login")
    cur.close(); conn.close()
    return render_template_string(RESET_HTML, message=None)

@app.route("/auth/users/<int:user_id>", methods=["PATCH"])
def update_user(user_id):
    if not require_admin():
        return jsonify({"error":"Solo admin"}), 403
    body=request.json or {}
    fields=[]; vals=[]
    if "role" in body:
        if body["role"] not in ["admin","teacher","student","trader"]:
            return jsonify({"error":"Rol inválido"}), 400
        fields.append("role=%s"); vals.append(body["role"])
    if "active" in body:
        current = decode_token()
        if current and current.get("user_id") == user_id and not bool(body["active"]):
            return jsonify({"error":"No puedes desactivar tu propio usuario admin"}), 400
        fields.append("active=%s"); vals.append(bool(body["active"]))
    if not fields:
        return jsonify({"error":"Sin cambios"}), 400
    vals.append(user_id)
    conn=get_db(); cur=conn.cursor(); cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=%s", vals); conn.commit(); cur.close(); conn.close()
    return jsonify({"message":"Usuario actualizado"})


@app.route("/auth/health")
def health():
    return jsonify({"status": "ok", "service": "quantlab_auth"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000)
