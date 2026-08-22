// 语音通话引擎（从原 voice_page.html 移植为 Vue3 composable）。
//
// 职责：WebSocket 协议处理、ASR 音频流推送、播报播放队列（按 sid 有序零间隙）、
// 开口即打断（ASR 内容级回声过滤 + 手动打断）、edge-tts 失败降级本地语音。
// 音色/VAD 等运行时配置来自后端 /api/config/voice。
import { onUnmounted, reactive, ref } from 'vue'
import { configApi, customApi } from '../../api'
import { getToken } from '../../api/http'
import {
  MOCK_START_RE,
  base64ToBytes,
  bytesToB64,
  calcRms,
  echoMatch,
  normalizeForEcho,
} from './voiceUtils'

const DEBUG_DEFAULT = false

export function useVoiceCall() {
  // ---------- UI 状态（响应式） ----------
  const ui = reactive({
    active: false,
    statusText: '未连接 · 点击下方按钮接通',
    statusBusy: false,
    statusInterruptible: false,
    waveOn: false,
    glowOn: false,
    micOn: false,
    micLevel: 0,
    timerText: '00:00',
    mode: '辅导答疑',
    transcript: [],
    debugOn: DEBUG_DEFAULT,
    debugLog: '',
    voiceReady: false,
    customJobTitle: '',
  })

  // ---------- 引擎状态（非响应式，音频帧高频写入） ----------
  const V = {
    ws: null,
    buf: '',
    speaking: false,
    bound: false,
    audioCtx: null,
    playingAudio: false,
    replyEnded: false,
    suppressAudio: false,
    sidData: {},
    playQueue: [],
    activeSources: [],
    nextPlaySid: 0,
    playTime: 0,
    pendingSources: 0,
    skippedSids: {},
    fallbackBySid: {},
    fallbackQueue: [],
    liveBubbleIndex: -1,
    timerStart: 0,
    timerInt: null,
    fallbackActive: 0,
    lastSend: 0,
    lastSendText: '',
    mic: null,
    micProc: null,
    asrReady: false,
    asrRetries: 0,
    asrSampleRate: 16000,
    lastAsrEvent: 0,
    vadAvg: 0,
    speakingText: '',
    prevSpeechText: '',
    echoUntil: 0,
    vadOn: false,
    analyser: null,
    vadData: null,
    vadHits: 0,
    quietTicks: 0,
    bargeArmed: false,
    noiseFloor: 0,
    bargeWait: 0,
    micLevel: 0,
    voicesWaiting: false,
    tts: 'edge',
  }

  // VAD 配置（后端下发，替代原 HTML 模板替换）
  const VAD = { threshold: 0.08, hits: 5, quietFrames: 3, noiseMargin: 1.6 }

  // ---------- 调试面板 ----------
  function dbg(msg) {
    if (!ui.debugOn) return
    const d = new Date()
    const ts =
      ('0' + d.getHours()).slice(-2) +
      ':' +
      ('0' + d.getMinutes()).slice(-2) +
      ':' +
      ('0' + d.getSeconds()).slice(-2)
    const head =
      'speaking:' + (V.speaking ? 'Y' : 'N') +
      ' mic:' + (V.mic ? 'Y' : 'N') +
      ' asr:' + (V.asrReady ? 'Y' : 'N') +
      ' lv:' + Math.round((V.micLevel || 0) * 260)
    ui.debugLog = head + '\n' + (ui.debugLog + '\n' + ts + ' ' + msg).split('\n').slice(-16).join('\n')
  }

  // ---------- 状态/UI ----------
  function setStatus(t, busy) {
    ui.statusText = t
    ui.statusBusy = !!busy
  }

  function setSpeakingUI(on) {
    ui.waveOn = on
    ui.glowOn = on
    ui.statusInterruptible = on // 播报中点击状态条 = 手动打断
  }

  function updateTimer() {
    if (!ui.active) return
    const s = Math.max(0, Math.floor((Date.now() - V.timerStart) / 1000))
    const m = Math.floor(s / 60)
    ui.timerText = ('0' + m).slice(-2) + ':' + ('0' + (s % 60)).slice(-2)
  }

  function addBubble(role, text) {
    ui.transcript.push({ role, text })
    if (role === 'assistant') V.liveBubbleIndex = ui.transcript.length - 1
    scrollTranscript()
    return ui.transcript.length - 1
  }

  function updateLive(text) {
    if (V.liveBubbleIndex < 0) V.liveBubbleIndex = addBubble('assistant', '')
    ui.transcript[V.liveBubbleIndex].text = text
    scrollTranscript()
  }

  function scrollTranscript() {
    // 交由视图层处理（通过 nextTick），这里不做 DOM
  }

  function setMode(m) {
    ui.mode = m
  }

  async function fetchCustomStatus() {
    try {
      const data = await customApi.status()
      ui.voiceReady = !!data.ready
      ui.customJobTitle = data.job_title || ''
      if (data.ready) {
        setMode('定制面试')
        const hint = ui.transcript.find((b) => b.role === 'hint')
        if (hint) hint.text = `已为你准备好「${data.job_title || '自定义岗位'}」定制面试，接通后小P会直接开始。`
      }
    } catch (e) {
      /* 忽略 */
    }
  }

  // ---------- 音频上下文 / 麦克风 ----------
  function ensureAudio() {
    if (!V.audioCtx) {
      try {
        const AC = window.AudioContext || window.webkitAudioContext
        V.audioCtx = new AC()
      } catch (e) {
        /* 忽略 */
      }
    }
  }

  function startAudioStream() {
    if (V.mic) return
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('请用 Chrome / Edge 浏览器')
      return
    }
    navigator.mediaDevices
      .getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      .then((stream) => {
        const AC = window.AudioContext || window.webkitAudioContext
        const ctx = V.audioCtx || new AC()
        if (ctx.state === 'suspended') {
          try {
            ctx.resume()
          } catch (e) {
            /* 忽略 */
          }
        }
        V.audioCtx = ctx
        const src = ctx.createMediaStreamSource(stream)
        const proc = ctx.createScriptProcessor(4096, 1, 1)
        proc.onaudioprocess = (e) => {
          const data = e.inputBuffer.getChannelData(0)
          const rms = calcRms(data)
          V.micLevel = rms
          updateMicMeter(rms)
          if (V.speaking) vadCheck(rms)
          if (!V.ws || V.ws.readyState !== 1 || !V.asrReady) return
          const pcm = new Int16Array(data.length)
          for (let i = 0; i < data.length; i++) {
            const s = Math.max(-1, Math.min(1, data[i]))
            pcm[i] = (s < 0 ? s * 0x8000 : s * 0x7fff) | 0
          }
          V.ws.send(JSON.stringify({ type: 'audio', data: bytesToB64(new Uint8Array(pcm.buffer)) }))
        }
        const g = ctx.createGain()
        g.gain.value = 0 // 静音输出，避免把采集声音播出来
        proc.connect(g)
        g.connect(ctx.destination)
        V.mic = stream
        V.micProc = proc
        ui.micOn = true
        if (V.ws && V.ws.readyState === 1) {
          V.asrSampleRate = ctx.sampleRate
          V.ws.send(JSON.stringify({ type: 'asr_start', sample_rate: ctx.sampleRate }))
          dbg('音频采集启动 sampleRate=' + ctx.sampleRate)
        }
      })
      .catch((e) => {
        setStatus('麦克风不可用: ' + (e && e.name) || e, true)
      })
  }

  function updateMicMeter(rms) {
    const lv = Math.min(100, Math.round(rms * 260))
    ui.micLevel = lv
  }

  function stopAudioStream() {
    if (V.micProc) {
      try {
        V.micProc.disconnect()
      } catch (e) {
        /* 忽略 */
      }
      V.micProc = null
    }
    if (V.mic) {
      try {
        V.mic.getTracks().forEach((t) => t.stop())
      } catch (e) {
        /* 忽略 */
      }
      V.mic = null
    }
    V.asrReady = false
    V.lastAsrEvent = 0
    ui.micOn = false
    ui.micLevel = 0
  }

  // 麦克风测试：录 2 秒并回放
  function micTest() {
    if (!V.mic) {
      setStatus('麦克风未启动，请先点「接通」', true)
      return
    }
    const ctx = V.audioCtx
    if (!ctx) return
    const rec = []
    const started = Date.now()
    const src = ctx.createMediaStreamSource(V.mic)
    const proc = ctx.createScriptProcessor(4096, 1, 1)
    const g = ctx.createGain()
    g.gain.value = 0
    proc.connect(g)
    g.connect(ctx.destination)
    src.connect(proc)
    proc.onaudioprocess = (e) => {
      const t = e.inputBuffer.getChannelData(0)
      for (let i = 0; i < t.length; i++) rec.push(t[i])
      if (Date.now() - started >= 2000) {
        try {
          src.disconnect()
          proc.disconnect()
        } catch (err) {
          /* 忽略 */
        }
        playRecorded(rec, ctx.sampleRate)
      }
    }
    setStatus('正在录音 2 秒…，请对着麦克风说话', true)
  }

  function playRecorded(samples, sr) {
    try {
      const ctx = V.audioCtx
      const buf = ctx.createBuffer(1, samples.length, sr)
      buf.copyToChannel(new Float32Array(samples), 0)
      const s = ctx.createBufferSource()
      s.buffer = buf
      s.connect(ctx.destination)
      s.start()
      setStatus('已回放你的声音（约 2 秒）')
    } catch (e) {
      setStatus('回放失败: ' + e, true)
    }
  }

  // 音量打断（兜底）：ASR 静默超 1.5s 时启用（原逻辑保留，实际由内容级打断承担）
  function vadCheck(rms) {
    if (V.lastAsrEvent && Date.now() - V.lastAsrEvent < 1500) return
    V.vadAvg = (V.vadAvg > 0 ? V.vadAvg : rms) * 0.995 + rms * 0.005
    const thr = Math.max(VAD.threshold, V.vadAvg * VAD.noiseMargin)
    if (rms > thr) V.vadHits++
    else V.vadHits = 0
    if (V.vadHits >= VAD.hits) {
      V.vadHits = 0
      dbg('音量打断 rms=' + rms.toFixed(3) + ' thr=' + thr.toFixed(3))
      bargeIn()
    }
  }

  // ---------- 发送 / ASR 处理 ----------
  function sendText(t) {
    if (!V.ws || V.ws.readyState !== 1) return
    const now = Date.now()
    if (V.lastSendText === t && now - V.lastSend < 2000) return
    V.lastSend = now
    V.lastSendText = t
    dbg('sendText: ' + t.slice(0, 20))
    stopAudio()
    V.speaking = false
    V.echoUntil = Date.now() + 2500
    V.replyEnded = false
    V.buf = ''
    V.liveBubbleIndex = -1
    addBubble('user', t)
    if (MOCK_START_RE.test(t)) setMode('模拟面试')
    V.ws.send(JSON.stringify({ type: 'text', content: t }))
    setStatus('小P思考中…', true)
  }

  function handleAsrText(text) {
    text = (text || '').trim()
    if (!text) return
    V.lastAsrEvent = Date.now()
    dbg('ASR句子: ' + text.slice(0, 16))
    if (V.prevSpeechText && Date.now() >= V.echoUntil) V.prevSpeechText = ''
    if (V.speaking || Date.now() < V.echoUntil) {
      if (isEchoLike(text)) {
        dbg('回声忽略(整句): ' + text.slice(0, 20))
        return
      }
      dbg('插话识别: ' + text.slice(0, 20))
    }
    sendText(text)
  }

  function isEchoLike(text) {
    const t = normalizeForEcho(text)
    if (!t) return false
    const b = normalizeForEcho(V.speakingText)
    if (echoMatch(t, b)) return true
    if (V.prevSpeechText && Date.now() < V.echoUntil) {
      const pb = normalizeForEcho(V.prevSpeechText)
      if (echoMatch(t, pb)) return true
    }
    return false
  }

  // ---------- 音频播放队列（按 sid 有序、零间隙） ----------
  function onAudioStart(sid, text) {
    if (V.suppressAudio) return
    V.replyEnded = false
    V.fallbackBySid[sid] = text || ''
    if (V.nextPlaySid === 0) V.nextPlaySid = sid
    V.vadAvg = 0
    if (!V.speaking) {
      dbg('播报开始: ' + (text || '').slice(0, 22))
      V.speaking = true
      setStatus('播报中…', true)
      setSpeakingUI(true)
      V.quietTicks = 0
      V.vadHits = 0
      V.bargeArmed = false
      V.noiseFloor = 0
      V.bargeWait = 0
    } else {
      V.quietTicks = 0
      V.vadHits = 0
    }
  }

  function onAudioFrame(sid, b64) {
    if (V.suppressAudio) return
    ensureAudio()
    if (!V.audioCtx) return
    const bytes = base64ToBytes(b64)
    const prev = V.sidData[sid]
    const merged = new Uint8Array((prev ? prev.length : 0) + bytes.length)
    if (prev) merged.set(prev, 0)
    merged.set(bytes, prev ? prev.length : 0)
    V.sidData[sid] = merged
  }

  function onAudioEnd(sid) {
    const bytes = V.sidData[sid]
    delete V.sidData[sid]
    delete V.fallbackBySid[sid]
    if (!bytes || !bytes.length) {
      if (!V.suppressAudio) {
        V.skippedSids[sid] = true
        scheduleReady()
      }
      return
    }
    ensureAudio()
    V.audioCtx.decodeAudioData(
      bytes.buffer.slice(0),
      (buf) => {
        if (V.suppressAudio) return
        V.playQueue.push({ sid, buf })
        V.playQueue.sort((a, b) => a.sid - b.sid)
        scheduleReady()
      },
      () => {
        if (!V.suppressAudio) {
          V.skippedSids[sid] = true
          scheduleReady()
        }
      },
    )
  }

  function scheduleReady() {
    if (V.suppressAudio) return
    if (V.fallbackActive > 0) return
    while (true) {
      if (V.skippedSids[V.nextPlaySid]) {
        delete V.skippedSids[V.nextPlaySid]
        V.nextPlaySid++
        continue
      }
      if (!V.playQueue.length || V.playQueue[0].sid !== V.nextPlaySid) break
      const item = V.playQueue.shift()
      playBuffer(item.buf)
      V.nextPlaySid++
    }
  }

  function playBuffer(buf) {
    const src = V.audioCtx.createBufferSource()
    src.buffer = buf
    src.connect(V.audioCtx.destination)
    const now = V.audioCtx.currentTime
    const t = Math.max(now + 0.02, V.playTime)
    src.start(t)
    V.activeSources.push(src)
    V.playTime = t + buf.duration
    V.pendingSources++
    V.playingAudio = true
    src.onended = () => {
      const i = V.activeSources.indexOf(src)
      if (i >= 0) V.activeSources.splice(i, 1)
      if (V.pendingSources > 0) V.pendingSources--
      if (V.pendingSources <= 0) {
        V.playingAudio = false
        audioDone()
      }
    }
  }

  function audioDone() {
    if (V.suppressAudio) return
    if (V.pendingSources > 0 || V.fallbackActive > 0) return
    if (V.fallbackQueue.length) {
      drainFallback()
      return
    }
    dbg('播报结束')
    V.speaking = false
    V.echoUntil = Date.now() + 2500
    setSpeakingUI(false)
    if (V.replyEnded && ui.active) setStatus('聆听中…')
  }

  function stopAudio() {
    V.playingAudio = false
    V.suppressAudio = true
    V.pendingSources = 0
    V.playQueue = []
    V.sidData = {}
    V.nextPlaySid = 0
    V.playTime = 0
    V.skippedSids = {}
    V.fallbackActive = 0
    V.fallbackBySid = {}
    V.fallbackQueue = []
    V.vadHits = 0
    V.vadAvg = 0
    setSpeakingUI(false)
    while (V.activeSources.length) {
      const s = V.activeSources.pop()
      try {
        s.stop()
        s.disconnect()
      } catch (e) {
        /* 忽略 */
      }
    }
    try {
      window.speechSynthesis.cancel()
    } catch (e) {
      /* 忽略 */
    }
  }

  // ---------- 打断 ----------
  function bargeIn() {
    if (!V.speaking) return
    dbg('打断触发')
    stopAudio()
    V.speaking = false
    V.buf = ''
    V.replyEnded = false
    V.liveBubbleIndex = -1
    V.echoUntil = Date.now() + 2500
    setSpeakingUI(false)
    if (V.ws && V.ws.readyState === 1) V.ws.send(JSON.stringify({ type: 'stop' }))
    setStatus('已打断，请继续')
  }

  // ---------- 降级本地语音 ----------
  function pickVoice() {
    const vs = window.speechSynthesis.getVoices()
    if (!vs || !vs.length) return null
    const prefs = ['Xiaoxiao', 'Xiaoyi', 'YunxiNeural', 'Xiaoyan', 'Huihui', 'Yaoyao', 'XiaoYun', 'Lili', 'zh-CN']
    for (const p of prefs) {
      for (const v of vs) {
        if ((v.name || '').indexOf(p) >= 0) return v
      }
    }
    for (const v of vs) {
      if ((v.lang || '').indexOf('zh') === 0) return v
    }
    return null
  }

  function speakFallback(text) {
    if (!text) return
    if (V.suppressAudio) return
    if (V.pendingSources > 0 || V.fallbackActive > 0) {
      V.fallbackQueue.push(text)
      return
    }
    startFallbackSpeech(text)
  }

  function drainFallback() {
    if (V.suppressAudio) return
    if (V.fallbackQueue.length) {
      startFallbackSpeech(V.fallbackQueue.shift())
      return
    }
    scheduleReady()
    audioDone()
  }

  function startFallbackSpeech(text) {
    if (!V.speaking) {
      V.speaking = true
      setStatus('播报中…', true)
      setSpeakingUI(true)
      V.quietTicks = 0
      V.vadHits = 0
      V.bargeArmed = false
      V.noiseFloor = 0
      V.bargeWait = 0
    }
    V.fallbackActive++
    const fallbackEnd = () => {
      V.fallbackActive = Math.max(0, V.fallbackActive - 1)
      drainFallback()
    }
    const doSpeak = () => {
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'zh-CN'
      u.rate = 0.95
      u.pitch = 1.1
      const voice = pickVoice()
      if (voice) u.voice = voice
      u.onend = fallbackEnd
      u.onerror = fallbackEnd
      try {
        window.speechSynthesis.speak(u)
      } catch (e) {
        fallbackEnd()
      }
    }
    const vs = window.speechSynthesis.getVoices()
    if ((!vs || !vs.length) && !V.voicesWaiting) {
      V.voicesWaiting = true
      const once = () => {
        V.voicesWaiting = false
        window.speechSynthesis.onvoiceschanged = null
        doSpeak()
      }
      window.speechSynthesis.onvoiceschanged = once
      setTimeout(once, 1500)
    } else {
      doSpeak()
    }
  }

  // ---------- WebSocket ----------
  async function loadConfig() {
    try {
      const cfg = await configApi.voice()
      VAD.threshold = cfg.vad_threshold
      VAD.hits = cfg.vad_hits
      VAD.quietFrames = cfg.vad_quiet_frames
      VAD.noiseMargin = cfg.vad_noise_margin
      V.tts = cfg.tts || 'edge'
    } catch (e) {
      /* 使用默认值 */
    }
  }

  function connect() {
    if (ui.active) return
    ensureAudio()
    try {
      window.speechSynthesis.getVoices()
    } catch (e) {
      /* 忽略 */
    }
    const proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://'
    const token = getToken()
    const url = proto + window.location.host + '/ws/voice?token=' + encodeURIComponent(token)
    ui.active = true
    setStatus('正在接通…', true)
    let ws
    try {
      ws = new WebSocket(url)
    } catch (e) {
      setStatus('无法连接语音服务', true)
      ui.active = false
      return
    }
    V.ws = ws
    V.lastSend = 0
    V.lastSendText = ''
    V.timerStart = Date.now()
    if (V.timerInt) clearInterval(V.timerInt)
    V.timerInt = setInterval(updateTimer, 1000)
    updateTimer()

    ws.onopen = () => {
      setStatus('正在接通…')
      startAudioStream()
    }

    ws.onmessage = (ev) => {
      let m
      try {
        m = JSON.parse(ev.data)
      } catch (e) {
        return
      }
      if (m.type === 'reply_start') {
        stopAudio()
        V.speaking = false
        V.prevSpeechText = V.speakingText || ''
        V.speakingText = ''
        V.replyEnded = false
        V.suppressAudio = false
        V.nextPlaySid = m.first_sid || 0
        setStatus('正在合成语音…', true)
      } else if (m.type === 'delta') {
        if (V.suppressAudio) return
        V.buf += m.content
        V.speakingText += m.content
        updateLive(V.buf)
      } else if (m.type === 'audio_start') {
        onAudioStart(m.sid, m.text)
      } else if (m.type === 'audio') {
        onAudioFrame(m.sid, m.data)
      } else if (m.type === 'audio_end') {
        onAudioEnd(m.sid)
      } else if (m.type === 'tts_error') {
        V.skippedSids[m.sid] = true
        scheduleReady()
        speakFallback(V.fallbackBySid[m.sid] || '')
      } else if (m.type === 'done') {
        if (V.suppressAudio) return
        V.replyEnded = true
        setTimeout(audioDone, 300)
      } else if (m.type === 'cancelled') {
        if (!V.suppressAudio) return
        V.buf = ''
        setStatus('已打断，请继续')
      } else if (m.type === 'asr_ready') {
        V.asrReady = true
        V.asrRetries = 0
        V.lastAsrEvent = Date.now()
        setStatus('聆听中…')
        dbg('ASR 就绪')
      } else if (m.type === 'asr_error') {
        V.asrReady = false
        V.lastAsrEvent = 0
        dbg('ASR错误: ' + (m.message || ''))
        setStatus('语音识别中断，自动重连中…', true)
      } else if (m.type === 'asr_text') {
        if (m.content) handleAsrText(m.content)
      } else if (m.type === 'asr_partial') {
        if (!V.speaking) return
        const pt = (m.content || '').trim()
        if (!pt) return
        V.lastAsrEvent = Date.now()
        dbg('ASR中间: ' + pt.slice(0, 16))
        if (isEchoLike(pt)) {
          dbg('回声忽略(中间): ' + pt.slice(0, 14))
          return
        }
        dbg('开口打断(中间结果): ' + pt.slice(0, 18))
        bargeIn()
      } else if (m.type === 'error') {
        if (m.message && m.message.indexOf('rate limit') >= 0) {
          setStatus('请求过于频繁，请稍候…', true)
        } else {
          setStatus('小P出错了: ' + (m.message || '未知错误'), true)
        }
      }
    }

    ws.onclose = () => {
      // 主动挂断不提示；异常断开提示可重连
      if (ui.active) {
        setStatus('连接已断开，点击可重连', true)
      }
      cleanupAudio()
    }

    ws.onerror = () => {
      setStatus('连接出错', true)
    }
  }

  function cleanupAudio() {
    stopAudio()
    stopAudioStream()
    V.speaking = false
    ui.waveOn = false
    ui.glowOn = false
    ui.statusInterruptible = false
    if (V.timerInt) {
      clearInterval(V.timerInt)
      V.timerInt = null
    }
  }

  function disconnect() {
    ui.active = false
    if (V.ws) {
      try {
        V.ws.close()
      } catch (e) {
        /* 忽略 */
      }
      V.ws = null
    }
    cleanupAudio()
    setStatus('未连接 · 点击下方按钮接通')
    ui.timerText = '00:00'
  }

  function onStatusClick() {
    // 播报中点击状态条 = 手动打断
    if (ui.statusInterruptible) bargeIn()
  }

  // 组件卸载清理
  onUnmounted(() => {
    disconnect()
  })

  // 初始化：加载运行时配置 + 定制面试状态
  loadConfig()
  fetchCustomStatus()

  return {
    ui,
    connect,
    disconnect,
    micTest,
    onStatusClick,
    fetchCustomStatus,
  }
}
