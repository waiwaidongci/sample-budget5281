import {studies,runs} from '../api/research.js';
import {clusterStudies,clusterRuns} from '../api/cluster.js';
export const store={studies:[],runs:[],studyId:0,runId:0,rows:[],clusterStudies:[],clusterRuns:[],clusterStudyId:0,clusterPlanId:0};
export async function load(){[store.studies,store.runs]=await Promise.all([studies.list(),runs.list()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;try{[store.clusterStudies,store.clusterRuns]=await Promise.all([clusterStudies.list(),clusterRuns.list()]);if(!store.clusterStudyId)store.clusterStudyId=store.clusterStudies[0]?.id||0;}catch(e){store.clusterStudies=[];store.clusterRuns=[];}}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
