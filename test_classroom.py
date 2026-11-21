import os,sys,tempfile
os.environ['VANBEIJING_DB']=tempfile.mktemp();os.environ['VANBEIJING_TEACHER_PASSWORD']='Van'
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from app import app,db
from fastapi.testclient import TestClient
c=TestClient(app)
assert c.post('/login',data={'nickname':'Van'}).status_code==400
assert c.post('/teacher-login',data={'username':'Van','password':'wrong'}).status_code==400
assert c.post('/teacher-login',data={'username':'Van','password':'Van'}).status_code==200
assert c.get('/api/classroom').json()['me']['role']=='teacher'
c.post('/logout')
for i in range(8):
 assert c.post('/login',data={'nickname':'Hoc vien '+str(i)}).status_code==200
 if i==0:
  uid=c.get('/api/classroom').json()['me']['user_id']
  body={'action':'answer','lesson':1,'question':'q1','answer':'0'}
  s=c.post('/api/classroom',json=body).json();assert s['correct']
  s=c.post('/api/classroom',json=body).json();assert s['progress'][0]['earned']==1
  assert c.get('/teacher').status_code==403
 else: assert c.get('/api/classroom').json()['progress']==[]
 c.post('/logout')
assert c.post('/login',data={'nickname':'Ninth'}).status_code==400
assert c.post('/login',data={'nickname':'  Hoc   vien 0 '}).status_code==200
assert c.get('/api/classroom').json()['me']['user_id']==uid
assert c.get('/api/classroom?history='+str(uid+1)).status_code==403
from classroom import QUESTIONS
for n,qs in QUESTIONS.items():
 for k,q in qs.items():
  if k.startswith('d'):
   r=c.post('/api/classroom',json={'action':'answer','lesson':int(n),'question':k,'answer':q['answer']});assert r.json()['correct'],(n,k)
c.post('/api/classroom',json={'action':'heartbeat','lesson':1,'tabId':'a'})
with db() as conn:conn.execute('UPDATE course_clock SET last_at=last_at-15')
c.post('/api/classroom',json={'action':'heartbeat','lesson':1,'tabId':'a'})
assert sum(x['seconds'] for x in c.get('/api/classroom').json()['activity'])==15
assert c.post('/login',data={'nickname':'X'},headers={'origin':'https://evil.example'}).status_code==403
print('PASS: teacher password, reserved nick, 8 students, profile reuse, isolation, 20 whole dictations, score deduplication, time and CSRF')
