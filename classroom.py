import json,re,time,unicodedata
from pathlib import Path
from fastapi import Request,HTTPException
from fastapi.responses import FileResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE=Path(__file__).parent
QUESTIONS=json.loads((BASE/'data/questions.json').read_text())
def norm(s):
 s=unicodedata.normalize('NFKC',s).lower()
 s=re.sub(r'(?m)^\s*[-•—]?\s*[^\n:：]{1,12}[:：]','',s)
 for a,b in [('三十五','35'),('三十六','36'),('三十二','32'),('八','8')]: s=s.replace(a,b)
 return ''.join(c for c in s if not unicodedata.category(c).startswith(('P','Z','C')))

def install(app):
 from app import db,require_user,templates
 with db() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS course_progress(user_id INTEGER,lesson INTEGER,question TEXT,earned INTEGER DEFAULT 0,tries INTEGER DEFAULT 0,answer TEXT DEFAULT '',assisted INTEGER DEFAULT 0,updated_at REAL,PRIMARY KEY(user_id,lesson,question));
  CREATE TABLE IF NOT EXISTS course_attempts(id INTEGER PRIMARY KEY,user_id INTEGER,lesson INTEGER,question TEXT,answer TEXT,correct INTEGER,assisted INTEGER,created_at REAL);
  CREATE TABLE IF NOT EXISTS course_activity(user_id INTEGER,day TEXT,lesson INTEGER,seconds INTEGER DEFAULT 0,PRIMARY KEY(user_id,day,lesson));
  CREATE TABLE IF NOT EXISTS course_clock(user_id INTEGER PRIMARY KEY,tab TEXT,last_at REAL,lesson INTEGER);
  ''')
 def snapshot(c,u):
  teacher=u['role']=='teacher'; uid=u['id']
  users=[dict(r) for r in c.execute("SELECT id AS user_id,username AS nickname,role FROM users WHERE role='student'" if teacher else 'SELECT id AS user_id,username AS nickname,role FROM users WHERE id=?',() if teacher else (uid,))]
  cond='' if teacher else ' WHERE user_id=?';args=() if teacher else (uid,)
  return {'me':{'user_id':uid,'nickname':u['full_name'],'role':u['role']},'users':users,'progress':[dict(r) for r in c.execute('SELECT * FROM course_progress'+cond,args)],'activity':[dict(r) for r in c.execute('SELECT * FROM course_activity'+cond,args)]}
 @app.middleware('http')
 async def csrf(request,call_next):
  if request.method=='POST':
   origin=request.headers.get('origin')
   if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
    from fastapi.responses import JSONResponse
    return JSONResponse({'error':'Nguồn yêu cầu không hợp lệ'},403)
  return await call_next(request)
 @app.get('/classroom',response_class=HTMLResponse)
 def classroom(request:Request):
  u=require_user(request)
  return templates.TemplateResponse(request,'classroom.html',{'user':u})
 @app.get('/learn',response_class=HTMLResponse)
 def learn(request:Request):
  u=require_user(request)
  return templates.TemplateResponse(request,'learn.html',{'user':u})
 @app.get('/course.html')
 def course(request:Request):
  require_user(request);return FileResponse(BASE/'course/course.html')
 @app.get('/course-bridge.js')
 def bridge():return FileResponse(BASE/'course/course-bridge.js',media_type='text/javascript')
 app.mount('/audio',StaticFiles(directory=BASE/'course/audio'))
 @app.get('/api/classroom')
 def get(request:Request,history:int=0):
  u=require_user(request)
  with db() as c:
   if history:
    if u['role']!='teacher' and history!=u['id']:raise HTTPException(403)
    return {'attempts':[dict(r) for r in c.execute('SELECT * FROM course_attempts WHERE user_id=? ORDER BY id DESC LIMIT 200',(history,))]}
   return snapshot(c,u)
 @app.post('/api/classroom')
 async def post(request:Request):
  u=require_user(request);body=await request.json(); action=body.get('action');uid=u['id'];now=time.time()
  lesson=body.get('lesson')
  if type(lesson)!=int or lesson<0 or lesson>10:raise HTTPException(400,'Bài không hợp lệ')
  with db() as c:
   c.execute('BEGIN IMMEDIATE')
   if action=='heartbeat':
    if u['role']=='teacher':return {'ok':True}
    tab=str(body.get('tabId',''))[:100];last=c.execute('SELECT * FROM course_clock WHERE user_id=?',(uid,)).fetchone()
    if last and last['tab']!=tab and now-last['last_at']<30:return {'ok':True}
    delta=int(now-last['last_at']) if last and last['tab']==tab and last['lesson']==lesson and 0<now-last['last_at']<=45 else 0
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    c.execute('INSERT INTO course_activity VALUES(?,?,?,?) ON CONFLICT(user_id,day,lesson) DO UPDATE SET seconds=seconds+excluded.seconds',(uid,day,lesson,min(delta,30)))
    c.execute('INSERT OR REPLACE INTO course_clock VALUES(?,?,?,?)',(uid,tab,now,lesson))
    return {'ok':True}
   if u['role']!='student':raise HTTPException(403,'Giáo viên chỉ xem bài')
   qid=body.get('question','');q=QUESTIONS.get(str(lesson),{}).get(qid)
   if not q:raise HTTPException(400,'Câu không hợp lệ')
   answer=str(body.get('answer',''))
   if len(answer)>15000:raise HTTPException(400,'Bài nhập quá dài')
   old=c.execute('SELECT * FROM course_progress WHERE user_id=? AND lesson=? AND question=?',(uid,lesson,qid)).fetchone()
   assisted=int(action=='reveal' or bool(old and old['assisted']))
   correct=int(norm(answer)==norm(q['answer'])) if action=='answer' else 0
   if action not in ('answer','reveal','draft'):raise HTTPException(400)
   earned=max(old['earned'] if old else 0,int(correct and not assisted))
   tries=(old['tries'] if old else 0)+int(action=='answer')
   if action=='reveal':answer=old['answer'] if old else ''
   c.execute('INSERT OR REPLACE INTO course_progress VALUES(?,?,?,?,?,?,?,?)',(uid,lesson,qid,earned,tries,answer,assisted,now))
   if action!='draft':c.execute('INSERT INTO course_attempts(user_id,lesson,question,answer,correct,assisted,created_at) VALUES(?,?,?,?,?,?,?)',(uid,lesson,qid,answer,correct,assisted,now))
   result=snapshot(c,u);result.update(correct=bool(correct),assisted=bool(assisted));return result
