<template>
  <div class="chat-page">
    <!-- 顶部：品牌 + 用户菜单 -->
    <header class="chat-header">
      <div class="brand">
        <div class="brand-avatar">
          <img src="/assets/avatar_small.png" alt="小P" />
        </div>
        <div>
          <div class="brand-name">面试官小P</div>
          <div class="brand-status"><i></i>在线</div>
        </div>
      </div>
      <div class="header-right">
        <el-button size="small" class="bank-btn" @click="bankVisible = true">
          📚 题库
        </el-button>
        <el-dropdown trigger="click" @command="onCommand">
          <span class="user-chip">
            <el-icon><User /></el-icon>
            {{ auth.nickname }}
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">个人资料</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div class="chat-layout">
      <!-- 侧栏：题库统计 + 历史 -->
      <aside class="chat-side">
        <div class="side-title">题库统计</div>
        <el-row :gutter="8">
          <el-col v-for="s in stats" :key="s.key" :span="12">
            <div class="stat-card">
              <div class="stat-num">{{ s.count }}</div>
              <div class="stat-label">{{ s.label }}</div>
            </div>
          </el-col>
        </el-row>
        <el-button class="side-btn" size="small" @click="bankVisible = true">
          📚 浏览题库 / 选题
        </el-button>

        <div class="side-title" style="margin-top: 18px">历史记录</div>
        <div v-if="!history.length" class="side-note">暂无面试记录</div>
        <div v-for="h in history" :key="h.id" class="rev-row">
          <span>{{ h.job_title || (h.mode === 'mock' ? '模拟面试' : '辅导答疑') }}</span>
          <span class="rev-meta">{{ fmtDate(h.started_at) }}</span>
          <span v-if="h.score != null" class="rev-score">{{ h.score }}</span>
        </div>

        <div class="side-note">数据按账号隔离 · 收藏/历史云端同步</div>
      </aside>

      <!-- 主区 -->
      <main class="chat-main">
        <div class="persona-row">
          <div class="persona-box">
            <span class="persona-label">面试官风格</span>
            <PersonaSelect v-model="persona" />
          </div>
          <div v-if="chat.active" class="mode-chip">
            {{ chat.finished ? '已结束 · 可开始新一轮' : `当前模式：${modeLabel}` }}
          </div>
          <el-button
            v-if="chat.active"
            size="small"
            text
            type="primary"
            @click="backHome"
          >
            🏠 返回首页
          </el-button>
        </div>

        <div v-if="chat.voiceReady" class="voice-ready-row">
          <div class="voice-ready">
            📞 定制面试已就绪：{{ chat.customJobTitle || '自定义' }}，接通电话即可开始语音面试
            <div class="voice-actions">
              <el-button size="small" type="primary" @click="goVoice">开始语音面试</el-button>
              <el-button size="small" @click="cancelVoice">取消</el-button>
            </div>
          </div>
        </div>

        <div ref="scrollRef" class="chat-scroll">
          <WelcomeCards v-if="!chat.active && !chat.history.length" @action="onWelcomeAction" />
          <ChatBubble
            v-for="(m, i) in chat.history"
            :key="i"
            :role="m.role"
            :content="m.content"
            :streaming="m.streaming"
          />
          <div v-if="chat.finished && chat.report" class="report-actions">
            <el-button type="primary" @click="downloadReport">下载总结报告 (.md)</el-button>
          </div>
        </div>

        <div class="chat-input-bar">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="输入你的回答或问题…（Enter 发送，Shift+Enter 换行）"
            :disabled="chat.sending"
            @keydown.enter.exact.prevent="send"
          />
          <el-button
            type="primary"
            class="send-btn"
            :loading="chat.sending"
            :disabled="!input.trim()"
            @click="send"
          >
            发送
          </el-button>
        </div>
      </main>
    </div>

    <!-- 语音入口 -->
    <a class="vc-float" title="打开语音通话" @click.prevent="goVoice">📞</a>

    <!-- 对话框 -->
    <QuestionBankDialog v-model:visible="bankVisible" @start="startComprehensive" />
    <CustomInterviewDialog
      v-model:visible="customVisible"
      @done="onCustomDone"
    />

    <!-- 个人资料 -->
    <el-dialog v-model="profileVisible" title="个人资料" width="min(420px, 92vw)">
      <el-form label-width="70px">
        <el-form-item label="用户名">
          <el-input :model-value="auth.user?.username" disabled />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="profile.nickname" />
        </el-form-item>
        <el-form-item label="默认人格">
          <PersonaSelect v-model="profile.persona" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="profileVisible = false">取消</el-button>
        <el-button type="primary" @click="saveProfile">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, User } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { bankApi, sessionApi } from '../api'
import PersonaSelect from '../components/PersonaSelect.vue'
import WelcomeCards from '../components/WelcomeCards.vue'
import ChatBubble from '../components/ChatBubble.vue'
import QuestionBankDialog from '../components/QuestionBankDialog.vue'
import CustomInterviewDialog from '../components/CustomInterviewDialog.vue'

const auth = useAuthStore()
const chat = useChatStore()
const router = useRouter()

const input = ref('')
const scrollRef = ref(null)
const bankVisible = ref(false)
const customVisible = ref(false)
const profileVisible = ref(false)
const profile = reactive({ nickname: '', persona: '' })

const persona = computed({
  get: () => chat.persona || profile.persona,
  set: (v) => {
    chat.persona = v
  },
})

const modeLabel = computed(() =>
  chat.mode === 'mock' ? '模拟面试' : '辅导答疑',
)

const stats = ref([])
const history = ref([])

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`
}

async function loadStats() {
  try {
    const m = await bankApi.meta()
    stats.value = m.sources.map((s) => ({ key: s.key, label: s.label, count: s.count }))
  } catch (e) {
    /* 忽略 */
  }
}

async function loadHistory() {
  try {
    const r = await sessionApi.history()
    history.value = r.items
  } catch (e) {
    /* 忽略 */
  }
}

async function scrollBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => chat.history.length,
  () => scrollBottom(),
)
watch(() => chat.sending, (s) => s && scrollBottom())

onMounted(async () => {
  await chat.load()
  await chat.refreshVoiceStatus()
  loadStats()
  loadHistory()
  if (auth.user?.persona) chat.persona = chat.persona || auth.user.persona
})

function onWelcomeAction(key) {
  if (key === 'mock') startMock()
  else if (key === 'coach') startCoach()
  else customVisible.value = true
}

async function startMock() {
  await chat.start({ mode: 'mock', persona: persona.value })
  await nextTick()
  scrollBottom()
}

async function startCoach() {
  await chat.start({ mode: 'coach', persona: persona.value })
  await nextTick()
  scrollBottom()
}

async function startComprehensive(titles) {
  bankVisible.value = false
  await chat.start({
    mode: 'mock',
    questions: titles,
    job_title: '综合练习',
    persona: persona.value,
  })
  ElMessage.success(`已挑选 ${titles.length} 道题开始综合面试`)
  await nextTick()
  scrollBottom()
}

async function onCustomDone() {
  await chat.refreshVoiceStatus()
  await nextTick()
  scrollBottom()
}

async function backHome() {
  await chat.reset()
  input.value = ''
}

async function send() {
  const msg = input.value.trim()
  if (!msg || chat.sending) return
  if (!chat.active) {
    // 无会话时默认进入辅导答疑
    await chat.start({ mode: 'coach', persona: persona.value })
  }
  input.value = ''
  await chat.send(msg)
}

function downloadReport() {
  if (!chat.report) return
  const blob = new Blob([chat.report], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')
  a.href = URL.createObjectURL(blob)
  a.download = `面试报告_${stamp}.md`
  a.click()
  URL.revokeObjectURL(a.href)
}

function goVoice() {
  router.push('/voice')
}

async function cancelVoice() {
  await chat.clearVoice()
}

async function onCommand(cmd) {
  if (cmd === 'logout') {
    await auth.logout()
    router.push('/login')
  } else if (cmd === 'profile') {
    profile.nickname = auth.user?.nickname || ''
    profile.persona = auth.user?.persona || ''
    profileVisible.value = true
  }
}

async function saveProfile() {
  await auth.updateMe({ nickname: profile.nickname, persona: profile.persona })
  if (profile.persona) chat.persona = profile.persona
  ElMessage.success('已保存')
  profileVisible.value = false
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 16px;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 4px 10px;
  flex: 0 0 auto;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #56637a;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid #e2e8f2;
  background: #fff;
}
.user-chip:hover {
  border-color: #9db2e8;
  color: #4f6ef7;
}
.bank-btn {
  border-radius: 999px;
}
.chat-layout {
  display: flex;
  gap: 16px;
  flex: 1 1 auto;
  min-height: 0;
}
.chat-side {
  width: 220px;
  flex: 0 0 220px;
  overflow-y: auto;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid #e7ecf4;
  border-radius: 16px;
  padding: 16px 14px;
}
.side-btn {
  width: 100%;
  margin-top: 12px;
}
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid #e7ecf4;
  border-radius: 16px;
  padding: 14px 18px 16px;
}
.persona-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
  margin-bottom: 10px;
}
.persona-box {
  display: flex;
  align-items: center;
  gap: 8px;
}
.persona-box :deep(.el-select) {
  width: 180px;
}
.persona-label {
  font-size: 13px;
  color: #7b879c;
  white-space: nowrap;
}
.mode-chip {
  font-size: 13px;
  color: #7b879c;
  letter-spacing: 0.4px;
}
.voice-ready-row {
  flex: 0 0 auto;
  margin-bottom: 10px;
}
.voice-actions {
  display: inline-flex;
  gap: 8px;
  margin-left: 12px;
}
.chat-scroll {
  flex: 1 1 auto;
  overflow-y: auto;
  min-height: 0;
  padding: 4px 4px 8px;
}
.report-actions {
  text-align: center;
  margin: 14px 0;
}
.chat-input-bar {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  flex: 0 0 auto;
  padding-top: 10px;
  border-top: 1px solid #eef1f7;
}
.chat-input-bar :deep(.el-textarea) {
  flex: 1;
}
.send-btn {
  width: 84px;
  height: 46px;
  border-radius: 12px;
}
.vc-float {
  position: fixed;
  bottom: 104px;
  right: 20px;
  z-index: 9999;
  width: 54px;
  height: 54px;
  border-radius: 50%;
  background: #4f6ef7;
  border: none;
  cursor: pointer;
  box-shadow: 0 4px 18px rgba(79, 110, 247, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  transition: all 0.25s;
  color: #fff;
  text-decoration: none;
}
.vc-float:hover {
  transform: scale(1.08);
  box-shadow: 0 6px 24px rgba(79, 110, 247, 0.5);
}

@media (max-width: 820px) {
  .chat-side {
    display: none;
  }
  .chat-page {
    padding: 0 8px;
  }
  .persona-box :deep(.el-select) {
    width: 140px;
  }
}
</style>
