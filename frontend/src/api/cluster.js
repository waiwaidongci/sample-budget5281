import {request,post} from './client.js';
export const clusterStudies={list:()=>request('/cluster-studies'),get:id=>request(`/cluster-studies/${id}`),create:x=>post('/cluster-studies',x),members:id=>request(`/cluster-studies/${id}/members`),addMember:(id,x)=>post(`/cluster-studies/${id}/members`,x),importMembers:(id,members)=>post(`/cluster-studies/${id}/members/import`,{members}),removeMember:(id,row)=>request(`/cluster-studies/${id}/members/${row}`,{method:'DELETE'}),plans:id=>request(`/cluster-studies/${id}/plans`),generate:(id,x)=>post(`/cluster-studies/${id}/plans`,x)};
export const clusterPlans={get:id=>request(`/cluster-plans/${id}`),publish:id=>post(`/cluster-plans/${id}/publish`,{})};
export const clusterRuns={list:(studyId)=>request('/cluster-runs'+(studyId?`?study_id=${studyId}`:'')),create:x=>post('/cluster-runs',x),get:id=>request(`/cluster-runs/${id}`)};
export const clusterClaims={list:()=>request('/cluster-claims')};
