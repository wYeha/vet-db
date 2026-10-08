import { createRouter, createWebHashHistory } from 'vue-router'

import HomeView from './views/HomeView.vue'
import ReaderView from './views/ReaderView.vue'
import SearchView from './views/SearchView.vue'
import PharmaView from './views/PharmaView.vue'
import PreparationView from './views/PreparationView.vue'
import DiseasesView from './views/DiseasesView.vue'
import DiseaseView from './views/DiseaseView.vue'
import OntologyView from './views/OntologyView.vue'
import VectorView from './views/VectorView.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/source/:id', name: 'reader', component: ReaderView, props: true },
  { path: '/search', name: 'search', component: SearchView },
  { path: '/pharma', name: 'pharma', component: PharmaView },
  { path: '/pharma/:id', name: 'preparation', component: PreparationView, props: true },
  { path: '/diseases', name: 'diseases', component: DiseasesView },
  { path: '/diseases/:id', name: 'disease', component: DiseaseView, props: true },
  { path: '/ontology', name: 'ontology', component: OntologyView },
  { path: '/vector', name: 'vector', component: VectorView },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})
