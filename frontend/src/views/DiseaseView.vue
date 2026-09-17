<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api/client'
import StructValue from '../components/StructValue.vue'

const props = defineProps({ id: { type: [String, Number], required: true } })

const disease = ref(null)
const loading = ref(true)
const error = ref('')

const SECTION_LABELS = {
  disease_name: 'Название',
  etiology: 'Этиология',
  pathogenesis: 'Патогенез',
  epidemiology: 'Эпизоотология',
  clinical_findings: 'Клинические признаки',
  differential_diagnosis: 'Дифференциальный диагноз',
  diagnosis: 'Диагностика',
  postmortem_findings: 'Патологоанатомические изменения',
  treatment: 'Лечение',
  prognosis: 'Прогноз',
  prevention: 'Профилактика',
  diagnostic_recommendations: 'Рекомендации по диагностике',
  treatment_recommendations: 'Рекомендации по лечению',
  notes_on_clinical_findings: 'Замечания по клинике',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    disease.value = await api.disease(props.id)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

const sections = computed(() => {
  const d = disease.value && disease.value.data
  if (!d || typeof d !== 'object') return []
  return Object.entries(d)
    .filter(([k]) => k !== 'disease_name')
    .map(([k, v]) => ({ key: k, label: SECTION_LABELS[k] || k.replace(/_/g, ' '), value: v }))
})

onMounted(load)
watch(() => props.id, load)
</script>

<template>
  <div v-if="loading" class="state">Загрузка…</div>
  <div v-else-if="error" class="state">Ошибка: {{ error }}</div>
  <div v-else-if="disease">
    <router-link to="/diseases">← К списку болезней</router-link>
    <h1>{{ disease.name }}</h1>
    <div class="meta" style="margin-bottom:12px">
      <span class="badge">{{ disease.species === 'avian' ? 'Птица' : 'Свиньи' }}</span>
    </div>

    <div v-if="!sections.length" class="state">Нет структурированных данных.</div>
    <div v-else class="panel">
      <div v-for="s in sections" :key="s.key" class="disease-section">
        <h3>{{ s.label }}</h3>
        <StructValue :value="s.value" />
      </div>
    </div>
  </div>
</template>
