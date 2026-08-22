<template>
  <el-dialog
    :model-value="visible"
    :title="'📚 题库'"
    width="min(1080px, 96vw)"
    top="4vh"
    destroy-on-close
    @update:model-value="emit('update:visible', $event)"
  >
    <el-collapse v-model="openPanels" class="bank-collapse">
      <!-- 添加自定义题 -->
      <el-collapse-item name="add">
        <template #title>
          <span>➕ 添加自定义题</span>
        </template>
        <el-form label-width="80px" label-position="left">
          <el-form-item label="题干">
            <el-input v-model="form.title" placeholder="如：如何设计一个限流组件？" />
          </el-form-item>
          <el-form-item label="参考答案">
            <el-input v-model="form.answer" type="textarea" :rows="3" placeholder="选填" />
          </el-form-item>
          <el-row :gutter="10">
            <el-col :span="8">
              <el-form-item label="标签">
                <el-input v-model="form.tags" placeholder="逗号分隔，如 Redis,限流" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="难度">
                <el-select v-model="form.difficulty">
                  <el-option label="简单" value="简单" />
                  <el-option label="中等" value="中等" />
                  <el-option label="困难" value="困难" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="公司">
                <el-input v-model="form.company" placeholder="选填" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item>
            <el-button type="primary" @click="submitAdd">添加到题库</el-button>
          </el-form-item>
        </el-form>
      </el-collapse-item>

      <!-- 批量导入 CSV -->
      <el-collapse-item name="import">
        <template #title>
          <span>📥 批量导入自定义题（CSV）</span>
        </template>
        <el-input
          v-model="csvText"
          type="textarea"
          :rows="4"
          placeholder="每行一题，至少包含「题干」；可选列：答案、标签（逗号分隔）、难度（简单/中等/困难）、公司。支持表头或无表头按列顺序。"
        />
        <el-button
          type="primary"
          class="import-btn"
          :loading="importing"
          @click="submitImport"
        >
          导入
        </el-button>
      </el-collapse-item>
    </el-collapse>

    <!-- 筛选 -->
    <el-row :gutter="10" class="filters">
      <el-col :span="6"><el-select v-model="filters.source" placeholder="来源" clearable><el-option v-for="s in meta.sources" :key="s.key" :label="s.label" :value="s.key" /></el-select></el-col>
      <el-col :span="6"><el-select v-model="filters.difficulty" placeholder="难度" clearable><el-option label="简单" value="简单" /><el-option label="中等" value="中等" /><el-option label="困难" value="困难" /></el-select></el-col>
      <el-col :span="6"><el-select v-model="filters.company" placeholder="公司" clearable filterable><el-option v-for="c in meta.companies" :key="c" :label="c" :value="c" /></el-select></el-col>
      <el-col :span="6"><el-input v-model="filters.keyword" placeholder="关键词，如 Redis / 索引" clearable @keyup.enter="load()" /></el-col>
    </el-row>
    <div class="tags-row">
      <el-select v-model="filters.tags" multiple collapse-tags collapse-tags-tooltip placeholder="标签筛选" class="tags-select">
        <el-option v-for="t in meta.tags" :key="t.name" :label="`${t.name} (${t.count})`" :value="t.name" />
      </el-select>
      <el-checkbox v-model="filters.favoriteOnly" label="⭐ 仅看收藏" />
      <el-button size="small" @click="load()">查询</el-button>
    </div>

    <!-- 题目列表 + 已选 -->
    <div class="bank-body">
      <div class="qlist">
        <el-empty v-if="!rows.length" description="暂无匹配的题目" :image-size="80" />
        <div v-for="q in rows" :key="q.id" class="qrow" :class="{ selected: isSelected(q.id) }">
          <div class="qinfo">
            <div class="qtitle-text">{{ q.title }}</div>
            <div class="qmeta">
              <el-tag size="small" :type="tagType(q.difficulty)">{{ q.difficulty }}</el-tag>
              <el-tag size="small" effect="plain">{{ q.source_label }}</el-tag>
              <span v-if="q.company" class="qcompany">{{ q.company }}</span>
            </div>
          </div>
          <div class="qops">
            <el-button
              size="small"
              :type="isSelected(q.id) ? 'primary' : 'default'"
              @click="toggleSelected(q)"
            >
              {{ isSelected(q.id) ? '✓ 已加入' : '加入面试' }}
            </el-button>
            <el-button size="small" :type="isFav(q.id) ? 'warning' : 'default'" @click="toggleFav(q)">
              {{ isFav(q.id) ? '★ 已收藏' : '☆ 收藏' }}
            </el-button>
          </div>
        </div>
      </div>

      <div v-if="selected.length" class="qsel">
        <div class="sel-title">🎯 已选题目（{{ selected.length }}）</div>
        <div v-for="(s, i) in selected" :key="s.id" class="sel-row">
          <span class="sel-idx">{{ i + 1 }}.</span>
          <span class="sel-title-text">{{ s.title }}</span>
          <el-button size="small" text type="danger" @click="removeSelected(s.id)">✕</el-button>
        </div>
        <el-button
          type="primary"
          class="sel-start"
          @click="emit('start', selected.map((s) => s.title))"
        >
          🚀 开始综合面试（{{ selected.length }} 题）
        </el-button>
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:visible', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { bankApi } from '../api'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'start'])

const openPanels = ref([])
const meta = reactive({ sources: [], companies: [], tags: [] })
const filters = reactive({
  source: '',
  difficulty: '',
  company: '',
  keyword: '',
  tags: [],
  favoriteOnly: false,
})
const rows = ref([])
const favoriteIds = ref(new Set())
const selected = ref([])
const form = reactive({ title: '', answer: '', tags: '', difficulty: '中等', company: '' })
const csvText = ref('')
const importing = ref(false)

const isSelected = (id) => selected.value.some((s) => s.id === id)
const isFav = (id) => favoriteIds.value.has(id)

function tagType(d) {
  if (d === '简单') return 'success'
  if (d === '困难') return 'danger'
  return 'warning'
}

async function loadMeta() {
  const m = await bankApi.meta()
  meta.sources = m.sources
  meta.companies = m.companies
  meta.tags = m.tags
}

async function load() {
  const r = await bankApi.browse({
    keyword: filters.keyword || undefined,
    source: filters.source || undefined,
    difficulty: filters.difficulty || undefined,
    company: filters.company || undefined,
    tags: filters.tags,
    favorite_only: filters.favoriteOnly || undefined,
    limit: 60,
  })
  rows.value = r.items
  favoriteIds.value = new Set(r.favorite_ids)
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      loadMeta()
      load()
    }
  },
)

onMounted(() => {
  if (props.visible) {
    loadMeta()
    load()
  }
})

function toggleSelected(q) {
  const i = selected.value.findIndex((s) => s.id === q.id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push({ id: q.id, title: q.title })
}
function removeSelected(id) {
  selected.value = selected.value.filter((s) => s.id !== id)
}

async function toggleFav(q) {
  if (favoriteIds.value.has(q.id)) {
    await bankApi.removeFavorite(q.id)
    favoriteIds.value.delete(q.id)
  } else {
    await bankApi.addFavorite(q.id)
    favoriteIds.value.add(q.id)
  }
}

async function submitAdd() {
  if (!form.title.trim()) {
    ElMessage.warning('请填写题干')
    return
  }
  await bankApi.add({
    title: form.title.trim(),
    answer: form.answer,
    tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
    difficulty: form.difficulty,
    company: form.company.trim(),
  })
  ElMessage.success('已添加到题库')
  form.title = form.answer = form.tags = form.company = ''
  loadMeta()
  load()
}

async function submitImport() {
  if (!csvText.value.trim()) {
    ElMessage.warning('请粘贴 CSV 内容')
    return
  }
  importing.value = true
  try {
    const stats = await bankApi.importCsv(csvText.value)
    ElMessage.success(`导入成功 ${stats.new} 条${stats.skipped ? `，跳过重复 ${stats.skipped} 条` : ''}`)
    csvText.value = ''
    loadMeta()
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '导入失败，请检查格式')
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.bank-collapse {
  margin-bottom: 12px;
}
.import-btn {
  margin-top: 10px;
}
.filters {
  margin-bottom: 10px;
}
.filters :deep(.el-select),
.filters :deep(.el-input) {
  width: 100%;
}
.tags-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.tags-select {
  width: 280px;
}
.bank-body {
  display: flex;
  gap: 14px;
  min-height: 320px;
}
.qlist {
  flex: 1;
  overflow-y: auto;
  max-height: 52vh;
  padding-right: 4px;
}
.qrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 1px solid #eef1f7;
  border-radius: 10px;
  padding: 8px 10px;
  margin-bottom: 8px;
  gap: 10px;
  background: #fff;
}
.qrow.selected {
  background: #eef2ff;
  border-left: 3px solid #4f6ef7;
}
.qinfo {
  min-width: 0;
}
.qtitle-text {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qmeta {
  margin-top: 3px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.qcompany {
  font-size: 12px;
  color: #8a95aa;
}
.qops {
  display: flex;
  gap: 6px;
  flex: 0 0 auto;
}
.qsel {
  flex: 0 0 260px;
  border: 1px solid #e7edf6;
  border-radius: 12px;
  padding: 10px;
  background: #fafcff;
  max-height: 52vh;
  overflow-y: auto;
}
.sel-title {
  font-weight: 700;
  margin-bottom: 8px;
  font-size: 13px;
}
.sel-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 13px;
}
.sel-idx {
  color: #8a95aa;
}
.sel-title-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sel-start {
  width: 100%;
  margin-top: 10px;
}
</style>
