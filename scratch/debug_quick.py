import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wx
import main
print('set env', flush=True)
os.environ['REMOTE_CONTROL_TOKEN']='secret'
os.environ['REMOTE_CONTROL_PORT']='0'
os.environ['AUTO_START_QUICK_TUNNEL']='1'
wx.CallAfter=lambda fn,*a,**k: fn(*a,**k)
print('patch thread', flush=True)
main.threading.Thread=lambda target=None,args=(),kwargs=None,daemon=None: type('T',(),{'start':lambda self: target(*args, **(kwargs or {})),'is_alive':lambda self: False})()
print('create app', flush=True)
app=wx.App(False)
print('create frame', flush=True)
f=main.ChatFrame()
print('frame created', flush=True)
