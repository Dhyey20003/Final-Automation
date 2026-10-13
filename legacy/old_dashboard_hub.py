"""Local launcher. Existing tools run in their own processes and interfaces."""
from pathlib import Path
import os, sys, json, subprocess, shutil, importlib.util, traceback, runpy
ROOT = Path(__file__).resolve().parents[1]
PATHS = json.loads((ROOT/'Dashboard/paths.json').read_text())
TOOLS = {
 'posts': ('Post Studio', 'Automations/Posts/relay_post_studio.py'),
 'video': ('Video Studio', 'Automations/Video/interview_studio_web.py'),
 'desktop': ('Video Desktop', 'Automations/Video/interview_studio.py'),
 'builder': ('Classic Video Builder', 'Automations/Video/build_interview.py'),
}
def interpreter():
 p=ROOT/'Data/Python/Scripts/python.exe'
 return str(p) if p.exists() else sys.executable

def environment():
 env=os.environ.copy()
 for k,p in {'TEMP':'Data/Temp','TMP':'Data/Temp','TMPDIR':'Data/Temp','HF_HOME':'Data/Cache/HuggingFace','TORCH_HOME':'Data/Cache/Torch','XDG_CACHE_HOME':'Data/Cache','PIP_CACHE_DIR':'Data/Cache/pip'}.items():
  dest=ROOT/p;dest.mkdir(parents=True,exist_ok=True);env[k]=str(dest)
 env['PATH']=str(ROOT/'Tools/ffmpeg')+os.pathsep+env.get('PATH','')
 env['PYTHONUTF8']='1'
 return env

def save_json(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8');temp.replace(path)

def prepare():
 for rel in list(PATHS['video'].values())+list(PATHS['posts'].values())+list(PATHS['images'].values()):
  p=ROOT/rel
  (p.parent if p.suffix else p).mkdir(parents=True,exist_ok=True)
 for name in ['config.json','config_with_captions.json']:
  p=ROOT/'Automations/Video'/name;d=json.loads(p.read_text(encoding='utf-8'))
  d['paths'].update({k:str(ROOT/v) for k,v in PATHS['video'].items()});save_json(p,d)
 for name,key in [('config.json','posts'),('image_generator_config.json','images')]:
  p=ROOT/'Automations/Posts'/name;d=json.loads(p.read_text(encoding='utf-8'));d.update({k:str(ROOT/v) for k,v in PATHS[key].items()})
  if key=='posts':
   candidates=[ROOT/'Tools/Tesseract-OCR/tesseract.exe',Path(d.get('tesseract_path',''))]
   if shutil.which('tesseract'): candidates.append(Path(shutil.which('tesseract')))
   for base in [os.environ.get('LOCALAPPDATA',''),os.environ.get('ProgramFiles','')]:
    if base: candidates.append(Path(base)/'Tesseract-OCR/tesseract.exe')
   for c in candidates:
    if c.is_file(): d['tesseract_path']=str(c);break
  save_json(p,d)

def setup(captions=False):
 import venv
 target=ROOT/'Data/Python'
 if not (target/'Scripts/python.exe').exists(): venv.EnvBuilder(with_pip=True).create(target)
 py=interpreter()
 packages=['faster-whisper'] if captions else ['pillow','openpyxl','pywin32','pytesseract']
 subprocess.run([py,'-m','pip','install',*packages],check=True,env=environment())
 print('\nSetup complete. Launch your tool from the dashboard.')

def check():
 env=environment();py=interpreter();rows=[]
 for mod,label in [('PIL','Pillow'),('openpyxl','Excel support'),('win32com','Outlook support'),('pytesseract','OCR Python support'),('faster_whisper','Local captions (optional)')]:
  r=subprocess.run([py,'-c',f'import {mod}'],capture_output=True,env=env)
  rows.append(('OK' if r.returncode==0 else 'MISSING')+'   '+label)
 for tool in ['ffmpeg','ffprobe']:
  rows.append(('OK' if shutil.which(tool,path=env['PATH']) else 'MISSING')+'   '+tool)
 c=json.loads((ROOT/'Automations/Posts/config.json').read_text())
 rows.append(('OK' if Path(c['tesseract_path']).is_file() else 'MISSING')+'   Tesseract executable')
 rows.append(('OK' if (ROOT/'Raw_Files/Employee_Data/Employee_Data.xlsx').is_file() else 'ADD FILE')+'   Employee_Data.xlsx')
 photos=[p for p in (ROOT/'Raw_Files/saved_profile').iterdir() if p.suffix.lower() in ['.png','.jpg','.jpeg','.bmp','.webp']]
 rows.append(f'{len(photos)}   Profile photos')
 return '\n'.join(rows)

def worker(action):
 os.environ.update(environment())
 try:
  if action in ['setup','captions']: setup(action=='captions')
  elif action=='check': print(check())
  else:
   prepare();_,rel=TOOLS[action];script=ROOT/rel
   os.chdir(script.parent);sys.path.insert(0,str(script.parent));sys.argv=[str(script)]
   runpy.run_path(str(script),run_name='__main__')
 except BaseException as e:
  if isinstance(e,SystemExit) and e.code in (None,0): return
  traceback.print_exc()
  from datetime import datetime
  path=ROOT/'Output/Logs'/('launcher_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.txt')
  path.write_text(traceback.format_exc(),encoding='utf-8')
  print('\nError details saved to:',path)
 finally:
  try: input('\nPress Enter to close this window...')
  except EOFError: pass

def gui():
 import tkinter as tk
 from tkinter import messagebox,filedialog
 prepare()
 win=tk.Tk();win.title('Relay | Automation Hub');win.geometry('1100x790');win.minsize(980,740)
 bg='#F4F6FC';navy='#11134B';blue='#3448DB';muted='#667085'
 win.configure(bg=bg);processes={}
 def label(parent,text,size=11,color=navy,bold=False,bgcolor=None,**kw):
  return tk.Label(parent,text=text,font=('Segoe UI',size,'bold' if bold else 'normal'),fg=color,bg=bgcolor or parent.cget('bg'),**kw)
 def button(parent,text,cmd,primary=False):
  b=tk.Button(parent,text=text,command=cmd,font=('Segoe UI',10,'bold'),bg=blue if primary else '#EDF0FC',fg='white' if primary else navy,activebackground='#DCE2FF',relief='flat',bd=0,padx=16,pady=10,cursor='hand2');return b
 def open_path(rel):
  try: os.startfile(str(ROOT/rel))
  except Exception as e: messagebox.showerror('Cannot open',str(e))
 def launch(key):
  if key in processes and processes[key].poll() is None:
   messagebox.showinfo('Already open','This tool is already open. Switch to its window or console.');return
  if key in ('video','desktop','builder'):
   if any(k in processes and processes[k].poll() is None for k in ('video','desktop','builder')):
    messagebox.showinfo('Video tool is open','Close the current video tool and its console before opening another video mode. They share working folders.');return
  try:
   prepare()
   processes[key]=subprocess.Popen([interpreter(),str(Path(__file__).resolve()),'--worker',key],cwd=ROOT,env=environment(),creationflags=subprocess.CREATE_NEW_CONSOLE if os.name=='nt' else 0)
   status.set('Opened '+TOOLS.get(key,(key.title(),))[0]+'. Use its window to continue.')
  except Exception as e: messagebox.showerror('Could not launch',str(e))
 header=tk.Frame(win,bg=navy,height=148);header.pack(fill='x');header.pack_propagate(False)
 label(header,'RELAY  /  WORKSPACE',10,'#AEB9FF',True).pack(anchor='w',padx=34,pady=(23,7))
 label(header,'Automation Hub',28,'white',True).pack(anchor='w',padx=32)
 label(header,'Your tools, inputs and finished work. One place to start.',11,'#CDD3F4').pack(anchor='w',padx=34,pady=(6,0))
 body=tk.Frame(win,bg=bg);body.pack(fill='both',expand=True,padx=30,pady=22)
 label(body,'What are you working on?',17,bold=True).pack(anchor='w',pady=(0,14))
 cards=tk.Frame(body,bg=bg);cards.pack(fill='x')
 specs=[('01  /  VIDEO','Interview videos','Trim clips, prepare captions and build\nfinished interview videos.','Open Video Studio','video','Open video outputs','Output/Videos'),('02  /  POSTS','Internal posts','Scan screenshots, manage logo replies,\ncreate posts and track publishing.','Open Post Studio','posts','Open post outputs','Output/Posts')]
 for i,(tag,title,desc,cta,key,folderlabel,folder) in enumerate(specs):
  cards.columnconfigure(i,weight=1,uniform='cards')
  card=tk.Frame(cards,bg='white',highlightbackground='#E0E5F0',highlightthickness=1,padx=22,pady=20);card.grid(row=0,column=i,sticky='nsew',padx=(0,10) if i==0 else (10,0))
  label(card,tag,10,blue,True).pack(anchor='w')
  label(card,title,20,bold=True).pack(anchor='w',pady=(9,7))
  label(card,desc,11,muted,justify='left').pack(anchor='w')
  button(card,cta,lambda k=key:launch(k),True).pack(fill='x',pady=(18,8))
  button(card,folderlabel,lambda p=folder:open_path(p)).pack(fill='x')
 row=tk.Frame(body,bg=bg);row.pack(fill='x',pady=(18,12))
 for title,path in [('Raw files','Raw_Files'),('Employee data','Raw_Files/Employee_Data'),('Profile photos','Raw_Files/saved_profile'),('Screenshots','Raw_Files/Posts'),('All outputs','Output')]:
  button(row,title,lambda p=path:open_path(p)).pack(side='left',padx=(0,8))
 label(body,'SETUP & OTHER TOOLS',10,muted,True).pack(anchor='w',pady=(7,8))
 row2=tk.Frame(body,bg=bg);row2.pack(fill='x')
 for title,key in [('Check requirements','check'),('Install core packages','setup'),('Install local captions','captions'),('Desktop video','desktop'),('Classic builder','builder')]:
  button(row2,title,lambda k=key:launch(k)).pack(side='left',padx=(0,6))
 def choose_ffmpeg():
  f=filedialog.askopenfilename(title='Select ffmpeg.exe (ffprobe.exe must be beside it)',filetypes=[('FFmpeg','ffmpeg.exe')])
  if not f:return
  source=Path(f).parent
  if not (source/'ffprobe.exe').is_file(): messagebox.showerror('Missing file','Select a folder containing both ffmpeg.exe and ffprobe.exe.');return
  for name in ['ffmpeg.exe','ffprobe.exe']:
   if (source/name).resolve()!=(ROOT/'Tools/ffmpeg'/name).resolve():shutil.copy2(source/name,ROOT/'Tools/ffmpeg'/name)
  messagebox.showinfo('FFmpeg ready','FFmpeg and FFprobe are now stored inside your hub.')
 def choose_ocr():
  f=filedialog.askopenfilename(title='Select your installed tesseract.exe',filetypes=[('Tesseract','tesseract.exe')])
  if f:
   p=ROOT/'Automations/Posts/config.json';d=json.loads(p.read_text());d['tesseract_path']=f;save_json(p,d)
   messagebox.showinfo('OCR configured','Tesseract location saved.')
 row3=tk.Frame(body,bg=bg);row3.pack(fill='x',pady=(8,0))
 for title,cmd in [('Add FFmpeg',choose_ffmpeg),('Locate Tesseract',choose_ocr),('Settings',lambda:open_path('Automations')),('Read instructions',lambda:open_path('START_HERE.txt'))]:button(row3,title,cmd).pack(side='left',padx=(0,8))
 status=tk.StringVar(value='Ready. Add your employee file and profile photos, then open a tool.')
 label(body,'',10,muted,textvariable=status,anchor='w').pack(fill='x',pady=(18,3))
 label(body,str(ROOT),9,muted,anchor='w').pack(fill='x')
 win.mainloop()

if __name__=='__main__':
 if len(sys.argv)>2 and sys.argv[1]=='--worker':worker(sys.argv[2])
 else:gui()
