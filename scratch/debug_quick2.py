import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wx
import main
print('start', flush=True)
os.environ['AUTO_START_QUICK_TUNNEL']='0'
app=wx.App(False)
f=main.ChatFrame()
f.Hide()
print('frame created with server', f._remote_ws_server is not None, flush=True)
os.environ['REMOTE_CONTROL_TOKEN']='secret'
os.environ['REMOTE_CONTROL_PORT']='0'
os.environ['AUTO_START_QUICK_TUNNEL']='1'
wx.CallAfter=lambda fn,*a,**k: fn(*a,**k)
main.threading.Thread=lambda target=None,args=(),kwargs=None,daemon=None: type('T',(),{'start':lambda self: target(*args, **(kwargs or {})),'is_alive':lambda self: False})()
statuses=[]
f.SetStatusText=lambda text: statuses.append(text)
class _FakeManager:
    def __init__(self, project_root, token, local_host, local_port, status_callback=None):
        self.public_ws_url='wss://demo.trycloudflare.com/ws?token=secret'
    def ensure_started(self):
        return 'https://demo.trycloudflare.com'
    def stop(self):
        pass
main.CloudflaredQuickTunnelManager=_FakeManager
print('calling start', flush=True)
f._start_remote_ws_server_if_configured()
print('after start', statuses, flush=True)
if f._remote_ws_server is not None:
    print('stopping', flush=True)
    f._remote_ws_server.stop()
    f._remote_ws_server=None
print('destroy', flush=True)
f.Destroy(); app.Destroy(); print('done', flush=True)
