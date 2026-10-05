"""Test-only 3-5 second WASAPI output capture; never records microphone input.

Prepare an isolated environment, without adding a product dependency:
  .venv/Scripts/python.exe -m venv ../.build-envs/read-state-audio
  ../.build-envs/read-state-audio/Scripts/python.exe -m pip install PyAudioWPatch
While the owned MC read-state runtime is ready, run:
  ../.build-envs/read-state-audio/Scripts/python.exe scripts/capture_read_state_tts.py --output <evidence>/no-unread-zdsr.wav --seconds 4 --trigger-url http://127.0.0.1:<control_port>

WASAPI may omit callback packets during silence. Packet timestamps align real
PCM with the bounded wall-clock window; omitted silent gaps are zero-filled,
never synthesized speech. Duration/rate/channels identify the sample count;
peak/RMS establish actual nonzero output. Chinese content still requires
listening to the actual artifact.
"""
import argparse, hashlib, json, math, struct, time, urllib.request, wave
from pathlib import Path
import pyaudiowpatch as audio

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
 p.add_argument('--seconds',type=float,default=4.0);p.add_argument('--trigger-url')
 args=p.parse_args()
 if not 3 <= args.seconds <= 5:p.error('capture must be 3-5 seconds')
 args.output.parent.mkdir(parents=True,exist_ok=True)
 with audio.PyAudio() as manager:
  device=manager.get_default_wasapi_loopback()
  if not device.get('isLoopbackDevice'):raise RuntimeError('default output loopback unavailable')
  channels=int(device['maxInputChannels']);rate=int(device['defaultSampleRate']);blocks=[]
  def receive(data,frames,timing,status):
   blocks.append((time.time(),data));return None,audio.paContinue
  stream=manager.open(format=audio.paInt16,channels=channels,rate=rate,input=True,
   input_device_index=device['index'],frames_per_buffer=1024,stream_callback=receive,start=False)
  try:
   started=time.time();stream.start_stream();quiet_found=False;ambient_rms=None
   if args.trigger_url:
    # Try a one-second quiet output window for up to three seconds. Ambient
    # output is diagnostic evidence, not a prerequisite for sending the key.
    quiet_deadline=time.monotonic()+3
    while True:
     time.sleep(1)
     # WASAPI emits no packets during digital silence. An empty recent
     # window is quiet, rather than a missing one-second sample failure.
     cutoff=time.time()-1
     window=b''.join(data for at,data in blocks if at>=cutoff)
     samples=struct.unpack('<'+'h'*(len(window)//2),window)
     level=math.sqrt(sum(v*v for v in samples)/max(1,len(samples)));ambient_rms=level
     if level<100:
      quiet_found=True;break
     if time.monotonic()>quiet_deadline:
      break
    # Discard all ambient samples. The artifact contains only the controlled
    # hotkey's bounded output window, even when other output never goes quiet.
    blocks.clear();started=time.time()
    request=urllib.request.Request(args.trigger_url,data=b'{"action":"hotkey"}',headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=5) as response:
     result=json.loads(response.read())
    if result.get('error'):raise RuntimeError('controlled native hotkey failed')
   elapsed=time.time()-started
   time.sleep(max(0,args.seconds-elapsed))
   stream.stop_stream()
  finally:stream.close()
 # Preserve the wall-clock capture window, including intervals where WASAPI
 # supplied no samples. Place actual packets by callback time without
 # compressing those silent intervals out of the recording.
 frame_bytes=channels*2
 raw=bytearray(round(args.seconds*rate)*frame_bytes)
 for at,data in blocks:
  offset=max(0,round((at-started)*rate)*frame_bytes-len(data))
  count=min(len(data),len(raw)-offset)
  if count>0:raw[offset:offset+count]=data[:count]
 raw=bytes(raw)
 with wave.open(str(args.output),'wb') as writer:
  writer.setnchannels(channels);writer.setsampwidth(2);writer.setframerate(rate);writer.writeframes(raw)
 values=struct.unpack('<'+'h'*(len(raw)//2),raw)
 meta={'captured_at_utc_epoch':started,'duration':len(raw)/(rate*channels*2),'sample_rate':rate,
  'channels':channels,'device_index':device['index'],'loopback':True,'native_hotkey_triggered':bool(args.trigger_url),
  'quiet_preroll_seconds':0,'quiet_window_found':quiet_found,'ambient_rms':ambient_rms,
  'peak':max((abs(v) for v in values),default=0),'rms':math.sqrt(sum(v*v for v in values)/max(1,len(values))),
  'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}
 args.output.with_suffix('.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
 print(json.dumps({'output':str(args.output),**meta}))
if __name__=='__main__':main()
