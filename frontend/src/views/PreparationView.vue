<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api/client'
import MarkdownView from '../components/MarkdownView.vue'

const props = defineProps({ id: { type: [String, Number], required: true } })

const prep = ref(null)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    prep.value = await api.preparation(props.id)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <div v-if="loading" class="state">Загрузка…</div>
  <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
  <div v-else-if="prep">
    <router-link to="/pharma">← К списку препаратов</router-link>
    <h1>{{ prep.trade_name || '—' }}</h1>
    <dl class="kv">
      <template v-if="prep.generic_name"><dt>Действующее вещество</dt><dd>{{ prep.generic_name }}</dd></template>
      <template v-if="prep.drug_class"><dt>Класс</dt><dd>{{ prep.drug_class }}</dd></template>
      <template v-if="prep.dosage_form"><dt>Форма</dt><dd>{{ prep.dosage_form }}</dd></template>
      <template v-if="prep.route"><dt>Путь введения</dt><dd>{{ prep.route }}</dd></template>
      <template v-if="prep.target_animals"><dt>Виды животных</dt><dd>{{ prep.target_animals }}</dd></template>
      <template v-if="prep.manufacturer"><dt>Производитель</dt><dd>{{ prep.manufacturer }}</dd></template>
      <template v-if="prep.reg_number"><dt>Рег. №</dt><dd>{{ prep.reg_number }}</dd></template>
      <dt>Источник</dt><dd><span class="badge">{{ prep.origin }}</span></dd>
    </dl>

    <h2 class="section-title">Инструкция</h2>
    <MarkdownView v-if="prep.instruction_md" :source="prep.instruction_md" />
    <div v-else class="state">Текст инструкции недоступен.</div>
  </div>
</template>
