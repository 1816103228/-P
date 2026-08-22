// 聊天会话状态：历史、模式、流式发送、定制面试生成。
import { defineStore } from 'pinia'
import { chatStream, customApi, sessionApi } from '../api'

export const useChatStore = defineStore('chat', {
  state: () => ({
    active: false,
    mode: '',
    history: [], // [{role:'assistant'|'user', content, streaming}]
    finished: false,
    report: null,
    persona: '',
    sending: false,
    voiceReady: false,
    customJobTitle: '',
  }),
  actions: {
    _setHistory(history) {
      this.history = (history || []).map(([role, content]) => ({
        role,
        content,
        streaming: false,
      }))
    },
    async load() {
      const s = await sessionApi.get()
      this.active = s.active
      this.mode = s.mode
      this.finished = s.finished
      this.report = s.report
      this.persona = s.persona || ''
      this._setHistory(s.history)
    },
    async start(body) {
      const r = await sessionApi.start(body)
      this.active = true
      this.mode = r.mode
      this.finished = false
      this.report = null
      this._setHistory(r.history)
    },
    async reset() {
      await sessionApi.reset()
      this.$reset()
    },
    /** 发送一条消息（SSE 流式），返回 Promise（done 事件或错误）。 */
    send(message) {
      return new Promise((resolve) => {
        this.history.push({ role: 'user', content: message, streaming: false })
        this.history.push({ role: 'assistant', content: '', streaming: true })
        this.sending = true
        chatStream(message, {
          onDelta: (d) => {
            const last = this.history[this.history.length - 1]
            last.content += d
          },
          onDone: (ev) => {
            const last = this.history[this.history.length - 1]
            last.streaming = false
            this.sending = false
            this.finished = !!ev.finished
            this.mode = ev.mode
            if (ev.finished) this.report = ev.report
            resolve(ev)
          },
          onError: (msg) => {
            const last = this.history[this.history.length - 1]
            last.content = last.content || '小P暂时无法回答，请稍后重试。'
            last.streaming = false
            this.sending = false
            resolve({ error: msg })
          },
        })
      })
    },
    /** 生成定制面试（SSE 进度），完成后进入该会话。 */
    async startCustom(jobTitle, jd, handlers = {}) {
      await customApi.generate(jobTitle, jd, {
        onProgress: (msg) => handlers.onProgress?.(msg),
        onError: (msg) => handlers.onError?.(msg),
        onDone: (ev) => {
          this.active = true
          this.mode = ev.mode
          this.finished = false
          this.report = null
          this._setHistory(ev.history)
          this.voiceReady = !!ev.custom_voice_ready
          this.customJobTitle = ev.job_title || ''
          handlers.onDone?.(ev)
        },
      })
    },
    async refreshVoiceStatus() {
      try {
        const s = await customApi.status()
        this.voiceReady = !!s.ready
        this.customJobTitle = s.job_title || ''
      } catch (e) {
        /* 忽略 */
      }
    },
    async clearVoice() {
      await customApi.clear()
      this.voiceReady = false
      this.customJobTitle = ''
    },
  },
})
