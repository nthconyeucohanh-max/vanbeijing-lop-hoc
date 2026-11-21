import os, json, sqlite3, hashlib, hmac, secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('VANBEIJING_DB', str(BASE / 'instance' / 'vanbeijing.db')))
DATA_PATH = BASE / 'data' / 'assignments.json'
PRODUCTION = os.getenv('VANBEIJING_PRODUCTION', '').lower() in ('1', 'true', 'yes', 'on')
SECRET_KEY = os.getenv('VANBEIJING_SECRET_KEY', 'dev-only-change-this-secret-before-deploy')
TEACHER_BOOTSTRAP_PASSWORD = os.getenv('VANBEIJING_TEACHER_PASSWORD', '')
if PRODUCTION:
    if len(SECRET_KEY) < 32 or SECRET_KEY == 'dev-only-change-this-secret-before-deploy':
        raise RuntimeError('Production needs VANBEIJING_SECRET_KEY (at least 32 characters).')
    if not TEACHER_BOOTSTRAP_PASSWORD:
        raise RuntimeError('Production needs VANBEIJING_TEACHER_PASSWORD (required).')

app = FastAPI(title='Tiếng Trung Vanbeijing - Bài tập online')
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site='lax', https_only=PRODUCTION, max_age=60*60*24*14)
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
app.mount('/assets', StaticFiles(directory=BASE/'assets'), name='assets')
templates = Jinja2Templates(directory=BASE/'templates')

with open(DATA_PATH, encoding='utf-8') as f:
    ASSIGNMENTS = json.load(f)
ASSIGNMENT_MAP = {a['id']: a for a in ASSIGNMENTS}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def hash_password(password: str, salt: Optional[str]=None):
    salt_b = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt_b, 150_000)
    return salt_b.hex(), dk.hex()

def verify_password(password: str, salt: str, digest: str):
    _, check = hash_password(password, salt)
    return hmac.compare_digest(check, digest)

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      full_name TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('teacher','student')),
      password_salt TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attempts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      assignment_id TEXT NOT NULL,
      attempt_no INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress','submitted')),
      answers_json TEXT NOT NULL DEFAULT '{}',
      started_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      submitted_at TEXT,
      active_seconds INTEGER NOT NULL DEFAULT 0,
      last_heartbeat TEXT,
      teacher_score REAL,
      teacher_feedback TEXT,
      graded_at TEXT,
      graded_by INTEGER,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(graded_by) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_attempt_user_assignment ON attempts(user_id, assignment_id, attempt_no);
    CREATE INDEX IF NOT EXISTS idx_attempt_status ON attempts(status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase ON users(username COLLATE NOCASE);
    ''')
    # Preserve existing teacher ID and submitted work when renaming the login.
    teacher = conn.execute("SELECT * FROM users WHERE role='teacher' ORDER BY id LIMIT 1").fetchone()
    pwd = TEACHER_BOOTSTRAP_PASSWORD or 'Van'
    if teacher:
        conn.execute("UPDATE users SET username='Van',full_name='Cô Van' WHERE id=?", (teacher['id'],))
    else:
        salt,digest=hash_password(pwd)
        conn.execute("INSERT INTO users(username,full_name,role,password_salt,password_hash,created_at) VALUES('Van','Cô Van','teacher',?,?,?)",(salt,digest,now_iso()))
    conn.executescript("""
    BEGIN IMMEDIATE;
    DROP TRIGGER IF EXISTS six_students;
    CREATE TRIGGER IF NOT EXISTS eight_students BEFORE INSERT ON users
    WHEN NEW.role='student' AND (SELECT count(*) FROM users WHERE role='student')>=8
    BEGIN SELECT RAISE(ABORT,'class_full'); END;
    """)
    conn.commit(); conn.close()

init_db()


def current_user(request: Request):
    uid = request.session.get('user_id')
    if not uid: return None
    conn=db(); row=conn.execute('SELECT id,username,full_name,role,active,created_at FROM users WHERE id=?',(uid,)).fetchone(); conn.close()
    return dict(row) if row and row['active'] else None

def require_user(request: Request, role: Optional[str]=None):
    user=current_user(request)
    if not user: raise HTTPException(status_code=401, detail='Chưa đăng nhập')
    if role and user['role']!=role: raise HTTPException(status_code=403, detail='Không có quyền truy cập')
    return user

def latest_attempt(conn, user_id, assignment_id, create=False):
    row=conn.execute('SELECT * FROM attempts WHERE user_id=? AND assignment_id=? ORDER BY attempt_no DESC LIMIT 1',(user_id,assignment_id)).fetchone()
    if not row and create:
        ts=now_iso()
        conn.execute('INSERT INTO attempts(user_id,assignment_id,attempt_no,status,answers_json,started_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (user_id,assignment_id,1,'in_progress','{}',ts,ts))
        conn.commit()
        row=conn.execute('SELECT * FROM attempts WHERE user_id=? AND assignment_id=? ORDER BY attempt_no DESC LIMIT 1',(user_id,assignment_id)).fetchone()
    return row

def format_seconds(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600); m, s = divmod(rem, 60)
    if h: return f'{h} giờ {m} phút'
    if m: return f'{m} phút {s} giây'
    return f'{s} giây'

def parse_iso(s):
    if not s: return None
    try: return datetime.fromisoformat(s)
    except: return None

templates.env.filters['duration'] = format_seconds
LOCAL_TZ = ZoneInfo(os.getenv('VANBEIJING_TIMEZONE','Asia/Ho_Chi_Minh'))
def local_time(value, fmt='%d/%m/%Y %H:%M'):
    if not value: return ''
    d=parse_iso(value) if isinstance(value,str) else value
    if not d: return str(value)
    if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
    return d.astimezone(LOCAL_TZ).strftime(fmt)
templates.env.filters['localtime'] = local_time

@app.exception_handler(401)
async def unauthorized(request, exc):
    return RedirectResponse('/login', status_code=303)

@app.get('/', response_class=HTMLResponse)
def root(request: Request):
    user=current_user(request)
    if not user: return RedirectResponse('/login',303)
    return RedirectResponse('/classroom',303)

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request): return RedirectResponse('/',303)
    return templates.TemplateResponse(request, 'login.html', {'error':None, 'teacher_error':None})

@app.post('/login', response_class=HTMLResponse)
def student_login(request: Request, nickname: str=Form(...)):
    nickname=' '.join(nickname.strip().split())
    if len(nickname) < 2 or len(nickname) > 40:
        return templates.TemplateResponse(request, 'login.html', {'error':'Tên/Nick cần từ 2 đến 40 ký tự.', 'teacher_error':None}, status_code=400)
    conn=db()
    row=conn.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(nickname,)).fetchone()
    if row and row['role']=='teacher':
        conn.close()
        return templates.TemplateResponse(request, 'login.html', {'error':'Tên/Nick này được dành cho giáo viên. Hãy chọn nick khác.', 'teacher_error':None}, status_code=400)
    if row and not row['active']:
        conn.close()
        return templates.TemplateResponse(request, 'login.html', {'error':'Tài khoản này đang bị tạm khóa. Hãy liên hệ giáo viên.', 'teacher_error':None}, status_code=400)
    if not row:
        salt,digest=hash_password(secrets.token_urlsafe(24))
        try:
            cur=conn.execute('INSERT INTO users(username,full_name,role,password_salt,password_hash,created_at) VALUES(?,?,?,?,?,?)',
                             (nickname,nickname,'student',salt,digest,now_iso()))
            conn.commit()
            row=conn.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            row=conn.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE',(nickname,)).fetchone()
            if not row:
                conn.close()
                return templates.TemplateResponse(request,'login.html',{'error':'Lớp đã đủ 8 học viên. Hãy nhập đúng nick đã dùng hoặc liên hệ cô giáo.','teacher_error':None},status_code=400)
    conn.close()
    request.session.clear()
    request.session['user_id']=row['id']
    return RedirectResponse('/classroom',303)

@app.post('/teacher-login', response_class=HTMLResponse)
def teacher_login(request: Request, username: str=Form(...), password: str=Form(...)):
    conn=db(); row=conn.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE AND role="teacher" AND active=1',(username.strip(),)).fetchone(); conn.close()
    if not row or not verify_password(password,row['password_salt'],row['password_hash']):
        return templates.TemplateResponse(request, 'login.html', {'error':None, 'teacher_error':'Sai tài khoản hoặc mật khẩu giáo viên.'}, status_code=400)
    request.session.clear()
    request.session['user_id']=row['id']
    return RedirectResponse('/classroom',303)

@app.post('/logout')
def logout(request: Request):
    request.session.clear(); return RedirectResponse('/login',303)


@app.get('/account', response_class=HTMLResponse)
def account_page(request: Request):
    user=require_user(request)
    return templates.TemplateResponse(request,'account.html',{'user':user,'error':None})

@app.post('/account', response_class=HTMLResponse)
def change_own_password(request: Request, current_password: str=Form(...), new_password: str=Form(...), confirm_password: str=Form(...)):
    user=require_user(request,'teacher')
    error=None
    if len(new_password)<8:
        error='Mật khẩu mới cần ít nhất 8 ký tự.'
    elif new_password != confirm_password:
        error='Hai lần nhập mật khẩu mới chưa khớp.'
    conn=db(); row=conn.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
    if not error and not verify_password(current_password,row['password_salt'],row['password_hash']):
        error='Mật khẩu hiện tại không đúng.'
    if error:
        conn.close(); return templates.TemplateResponse(request,'account.html',{'user':user,'error':error},status_code=400)
    salt,digest=hash_password(new_password)
    conn.execute('UPDATE users SET password_salt=?,password_hash=? WHERE id=?',(salt,digest,user['id'])); conn.commit(); conn.close()
    return RedirectResponse('/account?changed=1',303)

@app.get('/student', response_class=HTMLResponse)
def student_dashboard(request: Request):
    user=require_user(request,'student'); conn=db()
    cards=[]
    for a in ASSIGNMENTS:
        at=latest_attempt(conn,user['id'],a['id'])
        answers = json.loads(at['answers_json'] or '{}') if at else {}
        cards.append({'assignment':a,'attempt':dict(at) if at else None,'answered':sum(1 for v in answers.values() if str(v).strip()),'total_questions':sum(len(sec['questions']) for sec in a['sections'])})
    totals=conn.execute('SELECT COUNT(*) total, SUM(CASE WHEN status="submitted" THEN 1 ELSE 0 END) done, COALESCE(SUM(active_seconds),0) seconds FROM attempts WHERE user_id=?',(user['id'],)).fetchone()
    conn.close()
    return templates.TemplateResponse(request,'student_dashboard.html',{'user':user,'cards':cards,'totals':dict(totals),'assignment_count':len(ASSIGNMENTS)})

@app.get('/assignment/{assignment_id}', response_class=HTMLResponse)
def assignment_page(request: Request, assignment_id: str):
    user=require_user(request,'student')
    assignment=ASSIGNMENT_MAP.get(assignment_id)
    if not assignment: raise HTTPException(404)
    conn=db(); at=latest_attempt(conn,user['id'],assignment_id,create=True); conn.close()
    answers=json.loads(at['answers_json'] or '{}')
    return templates.TemplateResponse(request,'assignment.html',{'user':user,'assignment':assignment,'attempt':dict(at),'answers':answers})

@app.post('/assignment/{assignment_id}/submit')
async def submit_assignment(request: Request, assignment_id: str):
    user=require_user(request,'student')
    if assignment_id not in ASSIGNMENT_MAP: raise HTTPException(404)
    form=await request.form(); answers={}
    for k,v in form.multi_items():
        if k.startswith('q__'): answers[k[3:]]=v
    conn=db(); at=latest_attempt(conn,user['id'],assignment_id,create=True)
    ts=now_iso()
    conn.execute('UPDATE attempts SET answers_json=?,status="submitted",updated_at=?,submitted_at=?,last_heartbeat=? WHERE id=?',
                 (json.dumps(answers,ensure_ascii=False),ts,ts,ts,at['id']))
    conn.commit(); conn.close()
    return RedirectResponse('/student?submitted=1',303)

@app.post('/assignment/{assignment_id}/retry')
def retry_assignment(request: Request, assignment_id: str):
    user=require_user(request,'student')
    if assignment_id not in ASSIGNMENT_MAP: raise HTTPException(404)
    conn=db(); at=latest_attempt(conn,user['id'],assignment_id)
    next_no=(at['attempt_no']+1) if at else 1; ts=now_iso()
    conn.execute('INSERT INTO attempts(user_id,assignment_id,attempt_no,status,answers_json,started_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                 (user['id'],assignment_id,next_no,'in_progress','{}',ts,ts)); conn.commit(); conn.close()
    return RedirectResponse(f'/assignment/{assignment_id}',303)

@app.post('/api/autosave')
async def autosave(request: Request):
    user=require_user(request,'student'); payload=await request.json(); aid=payload.get('assignment_id'); answers=payload.get('answers',{})
    if aid not in ASSIGNMENT_MAP or not isinstance(answers,dict): return JSONResponse({'ok':False},400)
    conn=db(); at=latest_attempt(conn,user['id'],aid,create=True)
    if at['status']=='submitted': conn.close(); return {'ok':False,'submitted':True}
    conn.execute('UPDATE attempts SET answers_json=?,updated_at=? WHERE id=?',(json.dumps(answers,ensure_ascii=False),now_iso(),at['id'])); conn.commit(); conn.close()
    return {'ok':True,'saved_at':now_iso()}

@app.post('/api/heartbeat')
async def heartbeat(request: Request):
    user=require_user(request,'student'); payload=await request.json(); aid=payload.get('assignment_id')
    if aid not in ASSIGNMENT_MAP: return JSONResponse({'ok':False},400)
    conn=db(); at=latest_attempt(conn,user['id'],aid,create=True)
    now=datetime.now(timezone.utc); add=0
    last=parse_iso(at['last_heartbeat'])
    if last:
        delta=(now-last).total_seconds()
        if 0 < delta <= 90: add=min(int(delta),45)
    conn.execute('UPDATE attempts SET active_seconds=active_seconds+?,last_heartbeat=?,updated_at=? WHERE id=?',(add,now_iso(),now_iso(),at['id']))
    conn.commit(); new=conn.execute('SELECT active_seconds FROM attempts WHERE id=?',(at['id'],)).fetchone(); conn.close()
    return {'ok':True,'active_seconds':new['active_seconds']}

@app.get('/teacher', response_class=HTMLResponse)
def teacher_dashboard(request: Request):
    user=require_user(request,'teacher'); conn=db()
    students=[dict(x) for x in conn.execute('SELECT id,username,full_name,active,created_at FROM users WHERE role="student" ORDER BY full_name').fetchall()]
    rows=[]
    for st in students:
        ats=[dict(x) for x in conn.execute('''SELECT a.* FROM attempts a JOIN (SELECT assignment_id,MAX(attempt_no) n FROM attempts WHERE user_id=? GROUP BY assignment_id) z ON a.assignment_id=z.assignment_id AND a.attempt_no=z.n WHERE a.user_id=?''',(st['id'],st['id'])).fetchall()]
        amap={x['assignment_id']:x for x in ats}
        submitted=sum(1 for x in ats if x['status']=='submitted')
        active=sum(x['active_seconds'] or 0 for x in ats)
        rows.append({'student':st,'attempts':amap,'submitted':submitted,'active_seconds':active})
    recent=[dict(x) for x in conn.execute('''SELECT a.*,u.full_name,u.username FROM attempts a JOIN users u ON u.id=a.user_id WHERE u.role='student' ORDER BY a.updated_at DESC LIMIT 12''').fetchall()]
    conn.close()
    return templates.TemplateResponse(request,'teacher_dashboard.html',{'user':user,'rows':rows,'assignments':ASSIGNMENTS,'recent':recent,'assignment_titles':{a['id']:a['title'] for a in ASSIGNMENTS}})

@app.get('/teacher/student/{student_id}', response_class=HTMLResponse)
def teacher_student(request: Request, student_id: int):
    user=require_user(request,'teacher'); conn=db(); st=conn.execute('SELECT id,username,full_name,active,created_at FROM users WHERE id=? AND role="student"',(student_id,)).fetchone()
    if not st: conn.close(); raise HTTPException(404)
    details=[]
    for a in ASSIGNMENTS:
        ats=[dict(x) for x in conn.execute('SELECT * FROM attempts WHERE user_id=? AND assignment_id=? ORDER BY attempt_no DESC',(student_id,a['id'])).fetchall()]
        details.append({'assignment':a,'attempts':ats})
    conn.close()
    return templates.TemplateResponse(request,'teacher_student.html',{'user':user,'student':dict(st),'details':details})

@app.get('/teacher/review/{attempt_id}', response_class=HTMLResponse)
def review_attempt(request: Request, attempt_id: int):
    user=require_user(request,'teacher'); conn=db(); at=conn.execute('''SELECT a.*,u.full_name,u.username FROM attempts a JOIN users u ON u.id=a.user_id WHERE a.id=?''',(attempt_id,)).fetchone(); conn.close()
    if not at: raise HTTPException(404)
    assignment=ASSIGNMENT_MAP.get(at['assignment_id'])
    if not assignment: raise HTTPException(404)
    answers=json.loads(at['answers_json'] or '{}')
    return templates.TemplateResponse(request,'teacher_review.html',{'user':user,'attempt':dict(at),'assignment':assignment,'answers':answers})

@app.post('/teacher/review/{attempt_id}')
def grade_attempt(request: Request, attempt_id: int, score: str=Form(''), feedback: str=Form('')):
    user=require_user(request,'teacher')
    score_val=None
    if score.strip():
        try: score_val=float(score)
        except ValueError: score_val=None
    if score.strip() and (score_val is None or not 0 <= score_val <= 10):
        raise HTTPException(status_code=400, detail='Điểm phải từ 0 đến 10.')
    conn=db(); conn.execute('UPDATE attempts SET teacher_score=?,teacher_feedback=?,graded_at=?,graded_by=? WHERE id=?',(score_val,feedback.strip(),now_iso(),user['id'],attempt_id)); conn.commit(); conn.close()
    return RedirectResponse(f'/teacher/review/{attempt_id}?graded=1',303)

@app.get('/teacher/students', response_class=HTMLResponse)
def students_page(request: Request):
    user=require_user(request,'teacher'); conn=db(); students=[dict(x) for x in conn.execute('SELECT id,username,full_name,active,created_at FROM users WHERE role="student" ORDER BY created_at DESC').fetchall()]; conn.close()
    return templates.TemplateResponse(request,'teacher_students.html',{'user':user,'students':students,'error':None})

@app.post('/teacher/students', response_class=HTMLResponse)
def create_student(request: Request, nickname: str=Form(...)):
    user=require_user(request,'teacher'); nickname=' '.join(nickname.strip().split())
    error=None
    if len(nickname)<2 or len(nickname)>40: error='Tên/Nick cần từ 2 đến 40 ký tự.'
    conn=db()
    if not error:
        try:
            salt,digest=hash_password(secrets.token_urlsafe(24))
            conn.execute('INSERT INTO users(username,full_name,role,password_salt,password_hash,created_at) VALUES(?,?,?,?,?,?)',(nickname,nickname,'student',salt,digest,now_iso())); conn.commit()
        except sqlite3.IntegrityError: error='Tên/Nick này đã tồn tại.'
    students=[dict(x) for x in conn.execute('SELECT id,username,full_name,active,created_at FROM users WHERE role="student" ORDER BY created_at DESC').fetchall()]; conn.close()
    if error: return templates.TemplateResponse(request,'teacher_students.html',{'user':user,'students':students,'error':error},status_code=400)
    return RedirectResponse('/teacher/students?created=1',303)

@app.get('/health')
def health(): return {'ok':True,'assignments':len(ASSIGNMENTS)}

from classroom import install
install(app)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=int(os.getenv('PORT','8000')), reload=False)
