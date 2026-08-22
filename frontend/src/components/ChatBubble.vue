<template>
  <div class="msg" :class="role">
    <template v-if="role === 'assistant'">
      <div class="avatar">
        <img src="/assets/avatar_small.png" alt="小P" />
      </div>
      <div class="bubble">
        <div v-if="content" class="md" v-html="rendered"></div>
        <div v-else-if="streaming" class="typing-dots"><i></i><i></i><i></i></div>
      </div>
    </template>
    <template v-else>
      <div class="bubble">{{ content }}</div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps({
  role: { type: String, required: true }, // user | assistant
  content: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
})

const rendered = computed(() => renderMarkdown(props.content))
</script>

<style scoped>
.msg {
  display: flex;
  margin: 14px 0;
}
.msg.user {
  justify-content: flex-end;
}
.msg.user .bubble {
  background: linear-gradient(135deg, #4f6ef7, #6482f8);
  color: #fff;
  padding: 10px 15px;
  border-radius: 16px 16px 5px 16px;
  font-size: 15px;
  line-height: 1.7;
  box-shadow: 0 3px 10px rgba(79, 110, 247, 0.22);
  word-break: break-word;
  max-width: 82%;
  white-space: pre-wrap;
}
.msg.assistant {
  justify-content: flex-start;
}
.msg.assistant .avatar {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 50%;
  overflow: hidden;
  margin-right: 10px;
  margin-top: 4px;
  border: 2px solid #fff;
  box-shadow: 0 2px 8px rgba(31, 58, 102, 0.14);
}
.msg.assistant .avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 28%;
}
.msg.assistant .bubble {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e8edf6;
  border-radius: 16px 16px 16px 5px;
  padding: 12px 16px;
  box-shadow: 0 1px 4px rgba(20, 40, 80, 0.05);
  max-width: 96%;
  font-size: 15px;
  line-height: 1.7;
  word-break: break-word;
}
:deep(.md) p {
  margin: 0 0 0.4em;
}
:deep(.md) p:last-child {
  margin-bottom: 0;
}
:deep(.md) pre {
  background: #f6f8fb;
  border: 1px solid #e5eaf3;
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 13px;
}
:deep(.md) code {
  font-family: ui-monospace, Consolas, Menlo, monospace;
}
:deep(.md) table {
  border-collapse: collapse;
  margin: 6px 0;
}
:deep(.md) th,
:deep(.md) td {
  border: 1px solid #e2e8f2;
  padding: 4px 8px;
  font-size: 13px;
}
.typing-dots {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 2px 0;
}
.typing-dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9db2e8;
  animation: tp 1s ease-in-out infinite;
}
.typing-dots i:nth-child(2) {
  animation-delay: 0.15s;
}
.typing-dots i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes tp {
  0%,
  100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-3px);
  }
}
</style>
