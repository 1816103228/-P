<template>
  <div class="welcome">
    <div class="hero">
      <div class="hero-title">开始你的面试训练</div>
      <div class="hero-sub">模拟面试 · 辅导答疑 · 定制练习</div>
    </div>
    <el-row :gutter="14">
      <el-col v-for="card in cards" :key="card.title" :xs="24" :sm="8">
        <div class="qcard">
          <div class="qicon">{{ card.icon }}</div>
          <div class="qtitle">{{ card.title }}</div>
          <div class="qdesc">{{ card.desc }}</div>
          <el-button
            class="qbtn"
            type="primary"
            plain
            @click="emit('action', card.key)"
          >
            {{ card.label }}
          </el-button>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
const emit = defineEmits(['action'])

const cards = [
  { key: 'mock', icon: '🎤', title: '模拟面试', desc: '难度递进 · 逐题点评', label: '开始面试' },
  { key: 'coach', icon: '💡', title: '辅导答疑', desc: '标准回答 · 加分点 · 变式题', label: '开始答疑' },
  { key: 'custom', icon: '🎯', title: '定制面试', desc: '按岗位与 JD 生成专属题', label: '开始定制' },
]
</script>

<style scoped>
.hero {
  text-align: center;
  margin: 12px 0 10px;
}
.hero-title {
  font-size: 21px;
  font-weight: 700;
  color: #1e2c46;
  letter-spacing: 0.5px;
}
.hero-sub {
  margin-top: 6px;
  font-size: 13px;
  color: #7b879c;
  letter-spacing: 0.5px;
}
.qcard {
  background: #fff;
  border: 1px solid #e7edf6;
  border-radius: 16px;
  padding: 14px 12px 12px;
  text-align: center;
  box-shadow: 0 2px 10px rgba(20, 40, 80, 0.05);
  margin-bottom: 10px;
  transition: transform 0.15s, box-shadow 0.15s;
}
.qcard:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(20, 40, 80, 0.1);
}
.qicon {
  font-size: 26px;
  line-height: 1;
}
.qtitle {
  margin-top: 6px;
  font-size: 15px;
  font-weight: 700;
  color: #243352;
}
.qdesc {
  margin-top: 4px;
  font-size: 12px;
  color: #8792a7;
  min-height: 34px;
}
.qbtn {
  width: 100%;
  margin-top: 8px;
  border-radius: 10px;
}
</style>
