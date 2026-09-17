<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { api } from '../api/client'
import ConfirmModal from '../components/ConfirmModal.vue'

// messages: { role: 'user'|'assistant', text, hits?, error? }
const messages = ref([])
const input = ref('')
const loading = ref(false)
const llmConfigured = ref(true)
const feedEl = ref(null)

// История бесед
const CONV_KEY = 'vetai.chat.conversation_id'
const conversations = ref([])
const conversationId = ref(null)

function loadStoredId() {
  const raw = localStorage.getItem(CONV_KEY)
  const n = raw != null ? parseInt(raw, 10) : NaN
  conversationId.value = Number.isFinite(n) ? n : null
}

function setConversationId(id) {
  conversationId.value = id ?? null
  if (id) localStorage.setItem(CONV_KEY, String(id))
  else localStorage.removeItem(CONV_KEY)
}

async function refreshConversations() {
  try {
    conversations.value = await api.conversations({ limit: 50 })
  } catch (_) {
    conversations.value = [] // пустой список/ошибка не роняют UI
  }
}

onMounted(async () => {
  try {
    const h = await api.health()
    llmConfigured.value = !!h.llm_configured
  } catch (_) {
    llmConfigured.value = false
  }
  loadStoredId()
  await refreshConversations()
  if (conversationId.value) await openConversation(conversationId.value, false)
})

async function scrollDown() {
  await nextTick()
  if (feedEl.value) feedEl.value.scrollTop = feedEl.value.scrollHeight
}

// В историю для модели отдаём только текстовые реплики (без хитов/ошибок).
function buildHistory() {
  return messages.value
    .filter((m) => !m.error && m.text)
    .map((m) => ({ role: m.role, content: m.text }))
}

function mapMessages(rows) {
  return (rows || []).map((m) => ({
    role: m.role,
    text: m.content || '',
    hits: m.hits || [],
  }))
}

async function openConversation(id, scroll = true) {
  try {
    const detail = await api.conversation(id)
    messages.value = mapMessages(detail.messages)
    setConversationId(detail.id)
    if (scroll) await scrollDown()
  } catch (_) {
    // беседа могла исчезнуть/ошибка сети — не роняем UI, начинаем новый чат
    newChat()
  }
}

function newChat() {
  messages.value = []
  setConversationId(null)
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  const history = buildHistory()
  messages.value.push({ role: 'user', text })
  input.value = ''
  loading.value = true
  await scrollDown()
  try {
    const res = await api.chat({ message: text, history, conversation_id: conversationId.value })
    messages.value.push({
      role: 'assistant',
      text: res.answer || '',
      hits: res.hits || [],
    })
    if (res.conversation_id) {
      setConversationId(res.conversation_id)
      await refreshConversations()
    }
  } catch (e) {
    let msg = e.message || 'Ошибка запроса'
    if (e.status === 503) msg = 'Чат-ассистент пока не подключён (нет ключа модели).'
    else if (e.status === 429) msg = 'Суточный лимит ассистента исчерпан, попробуйте позже.'
    messages.value.push({ role: 'assistant', text: msg, error: true })
  } finally {
    loading.value = false
    await scrollDown()
  }
}

function hitLink(h) {
  if (h.type === 'page') {
    return { name: 'reader', params: { id: h.source_id }, query: { page: h.page_index } }
  }
  if (h.type === 'preparation') return { name: 'preparation', params: { id: h.ref_id } }
  if (h.type === 'disease') return { name: 'disease', params: { id: h.ref_id } }
  return { name: 'home' }
}

function hitTitle(h) {
  if (h.type === 'page') {
    const p = h.page_index != null ? ` → стр. ${h.page_index + 1}` : ''
    return `${h.source_title || 'Источник'}${p}`
  }
  return h.title || 'Без названия'
}

function convTitle(c) {
  return (c.title && c.title.trim()) || `Беседа #${c.id}`
}

// Очистка истории (кнопка + модальное подтверждение)
const confirmClear = ref(false)
async function clearHistory() {
  try {
    await api.clearConversations()
  } catch (_) {
    // ошибка очистки не должна ронять UI
  }
  await refreshConversations()
  newChat()
}
const clearButtons = [
  { label: 'Отмена', variant: 'secondary' },
  { label: 'Очистить', variant: 'danger', onClick: clearHistory },
]
</script>

<template>
  <div class="chat">
    <h1>Ассистент поиска</h1>

    <div v-if="!llmConfigured" class="state">
      Чат появится после подключения ключа модели. Пока пользуйтесь
      <router-link :to="{ name: 'search' }">обычным поиском</router-link>.
    </div>

    <div v-else class="chat-layout">
      <aside class="chat-sidebar">
        <div class="sidebar-head">
          <button type="button" class="btn" @click="newChat">Новый чат</button>
          <button
            type="button"
            class="icon-btn"
            title="Очистить историю"
            aria-label="Очистить историю"
            @click="confirmClear = true"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21" />
              <path d="M22 21H7" />
              <path d="m5 11 9 9" />
            </svg>
          </button>
        </div>
        <div class="conv-list">
          <p v-if="!conversations.length" class="muted conv-empty">Пока нет бесед</p>
          <button
            v-for="c in conversations"
            :key="c.id"
            type="button"
            :class="['conv-item', { active: c.id === conversationId }]"
            @click="openConversation(c.id)"
          >
            <span class="conv-title">{{ convTitle(c) }}</span>
            <span class="conv-meta">{{ c.message_count }}</span>
          </button>
        </div>
      </aside>

      <div class="chat-main">
        <div class="chat-feed" ref="feedEl">
          <p v-if="!messages.length" class="state">
            Опишите, что ищете — ассистент найдёт источник и страницу.
          </p>

          <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role, { error: m.error }]">
            <div class="bubble">
              <div class="msg-text">{{ m.text }}</div>
              <div v-if="m.hits && m.hits.length" class="chat-hits">
                <div v-for="(h, j) in m.hits" :key="j" class="hit">
                  <router-link :to="hitLink(h)">{{ hitTitle(h) }}</router-link>
                  <div v-if="h.snippet" class="snippet" v-html="h.snippet"></div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="loading" class="msg assistant">
            <div class="bubble"><div class="msg-text">Ищу…</div></div>
          </div>
        </div>

        <form class="chat-input" @submit.prevent="send">
          <input
            v-model="input"
            type="text"
            placeholder="Например: чем лечить парвовироз у щенка?"
            :disabled="loading"
          />
          <button type="submit" class="btn" :disabled="loading || !input.trim()">Отправить</button>
        </form>
      </div>
    </div>

    <ConfirmModal
      :open="confirmClear"
      title="Очистить историю?"
      message="Все сохранённые беседы будут удалены безвозвратно."
      :buttons="clearButtons"
      @close="confirmClear = false"
    />
  </div>
</template>
