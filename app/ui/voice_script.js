<script>
(function(){
var P=parent.window, d=parent.document;
var btn=d.getElementById('vccall'), st=d.getElementById('vcstatus');
if(!btn||!st)return;
var V=P.__voiceState;
if(!V){V=P.__voiceState={
  active:false,ws:null,rec:null,buf:'',speaking:false,bound:false,recPaused:false,
  audioCtx:null,playingAudio:false,replyEnded:false,suppressAudio:false,
  sidData:{},playQueue:[],activeSources:[],nextPlaySid:0,playTime:0,pendingSources:0,
  vadOn:false,vadHits:0,analyser:null,vadData:null,fallbackText:'',
  echoRef:0,echoTicks:0,echoCal:false
};}
var VAD_THRESHOLD=__VAD_THRESHOLD__;
var VAD_HITS=3;            // 连续超过阈值次数（约 180ms）才打断，避免瞬间噪声
var VAD_ECHO_FACTOR=1.8;   // 必须明显高于本段播报回声基准，才认为是真人在说话
var VAD_CALIB_MS=700;      // 每段播报开头测量回声基准的时间窗（毫秒）

function setStatus(t){if(st){st.textContent=t;st.style.display='block';}}
function render(){if(btn){btn.textContent=V.active?'⏹':'📞';btn.classList.toggle('on',V.active);}}

function ensureAudio(){
  if(!V.audioCtx){
    try{var AC=P.AudioContext||P.webkitAudioContext;V.audioCtx=new AC();}catch(e){}
  }
}
function pauseRec(){
  if(V.recPaused)return;
  V.recPaused=true;
  if(V.rec){try{V.rec.onend=null;V.rec.stop();}catch(e){}V.rec=null;}
}
function resumeRec(){
  if(!V.recPaused)return;
  V.recPaused=false;
  setTimeout(function(){
    if(V.active&&!V.speaking&&!V.recPaused&&!V.rec)startRec();
  },500);
}
function startRec(){
  var R=P.SpeechRecognition||P.webkitSpeechRecognition;
  if(!R){setStatus('请用 Chrome / Edge 浏览器');return;}
  if(V.rec)return;
  var rec=new R();
  rec.lang='zh-CN';rec.continuous=true;rec.interimResults=true;
  rec.onstart=function(){if(V.active)setStatus('聆听中…');};
  rec.onresult=function(e){
    var interim='',finalText='';
    for(var i=0;i<e.results.length;i++){
      var r=e.results[i];
      if(r.isFinal)finalText+=r[0].transcript;else interim+=r[0].transcript;
    }
    if(finalText)sendText(finalText);
    if((interim||finalText)&&V.active)setStatus('聆听中… '+(interim||finalText));
  };
  rec.onend=function(){if(V.active&&!V.recPaused){try{rec.start();}catch(e){}}};
  rec.onerror=function(ev){if(ev.error!=='no-speech')setStatus('语音识别错误: '+ev.error);};
  V.rec=rec;
  try{rec.start();}catch(e){}
}
function stopRec(){V.recPaused=false;if(V.rec){try{V.rec.onend=null;V.rec.stop();}catch(e){}V.rec=null;}}
function sendText(t){
  if(V.ws&&V.ws.readyState===1){
    V.replyEnded=false;V.buf='';
    V.ws.send(JSON.stringify({type:'text',content:t}));
    setStatus('小P思考中…');
  }
}

/* ---- 音频：按 sid 缓冲、按序零间隙播放（消除句间停顿）---- */
function base64ToBytes(b64){
  var bin=atob(b64),bytes=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
  return bytes;
}
function onAudioStart(sid,text){
  V.replyEnded=false;V.suppressAudio=false;V.fallbackText=text||'';
  if(V.nextPlaySid===0)V.nextPlaySid=sid;
  if(!V.speaking){pauseRec();V.speaking=true;setStatus('播报中…');}
  // 新一段播报：重置回声基准
  V.echoRef=0;V.echoTicks=0;V.echoCal=true;
}
function onAudioFrame(sid,b64){
  ensureAudio();
  if(!V.audioCtx||V.suppressAudio)return;
  var bytes=base64ToBytes(b64);
  var prev=V.sidData[sid];
  var merged=new Uint8Array((prev?prev.length:0)+bytes.length);
  if(prev)merged.set(prev,0);
  merged.set(bytes,prev?prev.length:0);
  V.sidData[sid]=merged;
}
function onAudioEnd(sid){
  var bytes=V.sidData[sid];
  delete V.sidData[sid];
  if(!bytes||!bytes.length)return;
  ensureAudio();
  V.audioCtx.decodeAudioData(bytes.buffer.slice(0),function(buf){
    if(V.suppressAudio)return;
    V.playQueue.push({sid:sid,buf:buf});
    V.playQueue.sort(function(a,b){return a.sid-b.sid;});
    scheduleReady();
  },function(){});
}
function scheduleReady(){
  if(V.suppressAudio)return;
  while(V.playQueue.length&&V.playQueue[0].sid===V.nextPlaySid){
    var item=V.playQueue.shift();
    playBuffer(item.buf);
    V.nextPlaySid++;
  }
}
function playBuffer(buf){
  var src=V.audioCtx.createBufferSource();
  src.buffer=buf;src.connect(V.audioCtx.destination);
  var now=V.audioCtx.currentTime;
  var t=Math.max(now+0.02,V.playTime); // 紧接上一段结束时间，零间隙
  src.start(t);
  V.activeSources.push(src); // 记录正在/将要播放的源，挂断时可强制停止
  V.playTime=t+buf.duration;
  V.pendingSources++;
  V.playingAudio=true;
  src.onended=function(){
    var i=V.activeSources.indexOf(src);
    if(i>=0)V.activeSources.splice(i,1);
    if(V.pendingSources>0)V.pendingSources--;
    if(V.pendingSources<=0){V.playingAudio=false;audioDone();}
  };
}
function audioDone(){
  if(V.pendingSources>0)return;
  V.speaking=false;
  if(V.replyEnded&&V.active){setStatus('聆听中…');resumeRec();}
}
function stopAudio(){
  V.playingAudio=false;V.suppressAudio=true;
  V.pendingSources=0;V.playQueue=[];V.sidData={};
  V.nextPlaySid=0;V.playTime=0;
  while(V.activeSources.length){var s=V.activeSources.pop();try{s.stop();s.disconnect();}catch(e){}}
  try{P.speechSynthesis.cancel();}catch(e){}
}

/* ---- 打断：VAD + 自适应回声基准（不被自己的播报误触发）---- */
function bargeIn(){
  if(!V.speaking)return;
  stopAudio();
  V.speaking=false;V.buf='';V.replyEnded=false;
  if(V.ws&&V.ws.readyState===1){V.ws.send(JSON.stringify({type:'stop'}));}
  setStatus('已打断，请继续');
  resumeRec();
}
function initVAD(){
  if(V.vadOn)return;
  try{
    P.navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}})
    .then(function(stream){
      V.vadOn=true;
      ensureAudio();
      var srcNode=V.audioCtx.createMediaStreamSource(stream);
      V.analyser=V.audioCtx.createAnalyser();
      V.analyser.fftSize=1024;
      srcNode.connect(V.analyser);
      V.vadData=new Uint8Array(V.analyser.fftSize);
      vadLoop();
    }).catch(function(){V.vadOn=false;});
  }catch(e){}
}
function vadLoop(){
  if(!V.active){V.vadHits=0;setTimeout(vadLoop,200);return;}
  if(V.analyser&&V.vadData){
    V.analyser.getByteTimeDomainData(V.vadData);
    var sum=0;
    for(var i=0;i<V.vadData.length;i++){var v=(V.vadData[i]-128)/128;sum+=v*v;}
    var rms=Math.sqrt(sum/V.vadData.length);
    if(V.speaking){
      if(V.echoCal){
        // 播报开头校准本机回声音量基准
        V.echoTicks++;
        if(rms>V.echoRef)V.echoRef=rms;
        if(V.echoTicks*60>=VAD_CALIB_MS)V.echoCal=false;
      }else{
        var limit=Math.max(VAD_THRESHOLD,V.echoRef*VAD_ECHO_FACTOR);
        if(rms>limit){V.vadHits=(V.vadHits||0)+1;}else{V.vadHits=0;}
        if(V.vadHits>=VAD_HITS){V.vadHits=0;bargeIn();}
      }
    }else{V.vadHits=0;}
  }
  setTimeout(vadLoop,60);
}

/* ---- 回退：edge-tts 失败时用本地语音（自动挑选最自然的中文音色）---- */
function pickVoice(){
  var vs=P.speechSynthesis.getVoices();
  if(!vs||!vs.length)return null;
  var prefs=['Xiaoxiao','Xiaoyi','YunxiNeural','Xiaoyan','Huihui','Yaoyao','XiaoYun','Lili','zh-CN'];
  for(var i=0;i<prefs.length;i++){
    for(var j=0;j<vs.length;j++){
      var name=vs[j].name||'';
      if(name.indexOf(prefs[i])>=0)return vs[j];
    }
  }
  for(var k=0;k<vs.length;k++){if((vs[k].lang||'').indexOf('zh')===0)return vs[k];}
  return null;
}
function speakFallback(text){
  if(!text)return;
  if(!V.speaking){pauseRec();V.speaking=true;setStatus('播报中…');}
  var u=new SpeechSynthesisUtterance(text);
  u.lang='zh-CN';u.rate=0.95;u.pitch=1.1;
  var voice=pickVoice();if(voice)u.voice=voice;
  u.onend=function(){V.speaking=false;if(V.replyEnded){setStatus('聆听中…');resumeRec();}};
  u.onerror=function(){V.speaking=false;if(V.replyEnded){setStatus('聆听中…');resumeRec();}};
  try{P.speechSynthesis.speak(u);}catch(e){}
}

function connect(){
  ensureAudio();
  initVAD();
  try{P.speechSynthesis.getVoices();}catch(e){}
  var proto=(P.location.protocol==='https:')?'wss://':'ws://';
  var url=proto+P.location.hostname+':__VOICE_PORT__/ws/voice';
  V.active=true;render();setStatus('正在接通…');
  var ws;
  try{ws=new WebSocket(url);}catch(e){setStatus('无法连接语音服务');V.active=false;render();return;}
  V.ws=ws;
  ws.onopen=function(){setStatus('通话已接通，请说话');startRec();};
  ws.onmessage=function(ev){
    var m;
    try{m=JSON.parse(ev.data);}catch(e){return;}
    if(m.type==='delta')V.buf+=m.content;
    else if(m.type==='audio_start')onAudioStart(m.sid,m.text);
    else if(m.type==='audio')onAudioFrame(m.sid,m.data);
    else if(m.type==='audio_end')onAudioEnd(m.sid);
    else if(m.type==='tts_error')speakFallback(V.fallbackText);
    else if(m.type==='done'){V.replyEnded=true;setTimeout(audioDone,300);}
    else if(m.type==='cancelled'){V.buf='';setStatus('已打断，请继续');}
    else if(m.type==='error')setStatus('小P出错了: '+m.message);
  };
  ws.onclose=function(){
    var wasActive=V.active;
    V.ws=null;stopRec();stopAudio();
    V.buf='';V.speaking=false;V.active=false;render();
    if(wasActive)setStatus('通话已断开');
  };
  ws.onerror=function(){setStatus('连接语音服务失败，请确认已启动 voice_server');};
}
function hangup(){
  V.active=false;render();
  stopRec();stopAudio();
  V.speaking=false;V.buf='';
  if(V.ws){try{V.ws.close();}catch(e){}}
  V.ws=null;
  setStatus('已挂断');setTimeout(function(){if(st)st.style.display='none';},1500);
}
if(!V.bound){
  V.bound=true;
  btn.onclick=function(){
    if(!V.active){connect();return;}
    hangup(); // 按钮=挂断；语音打断由 VAD（开口说话）负责
  };
}
render();
})();
</script>
