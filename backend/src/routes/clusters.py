from fastapi import APIRouter,Depends

from src.config.database import get_db
from src.middlewares.auth import current_user,writer
from src.schemas.cluster import (
    HouseholdStudyInput,AudienceMemberInput,AudienceImportInput,PlanGenerateInput,ClusterRunInput,
)
from src.services import clusters

router=APIRouter()

@router.get('/cluster-studies')
def list_cluster_studies(db=Depends(get_db),user=Depends(current_user)):
    return clusters.list_studies(db)

@router.post('/cluster-studies')
def create_cluster_study(data:HouseholdStudyInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.create_study(db,data,user)

@router.get('/cluster-studies/{id}')
def get_cluster_study(id:int,db=Depends(get_db),user=Depends(current_user)):
    return clusters.get_study(db,id)

@router.get('/cluster-studies/{id}/members')
def list_members(id:int,db=Depends(get_db),user=Depends(current_user)):
    return clusters.members(db,id)

@router.post('/cluster-studies/{id}/members')
def add_member(id:int,data:AudienceMemberInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.add_member(db,id,data,user)

@router.post('/cluster-studies/{id}/members/import')
def import_members(id:int,data:AudienceImportInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.import_members(db,id,data,user)

@router.put('/cluster-studies/{id}/members/{member_id}')
def update_member(id:int,member_id:int,data:AudienceMemberInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.update_member(db,id,member_id,data,user)

@router.delete('/cluster-studies/{id}/members/{member_id}')
def delete_member(id:int,member_id:int,db=Depends(get_db),user=Depends(writer)):
    return clusters.delete_member(db,id,member_id,user)

@router.get('/cluster-studies/{id}/plans')
def list_plans(id:int,db=Depends(get_db),user=Depends(current_user)):
    return clusters.list_plans(db,id)

@router.post('/cluster-studies/{id}/plans')
def generate_plan(id:int,data:PlanGenerateInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.generate_plan(db,id,data,user)

@router.get('/cluster-plans/{id}')
def get_plan(id:int,db=Depends(get_db),user=Depends(current_user)):
    return clusters.get_plan(db,id)

@router.post('/cluster-plans/{id}/publish')
def publish_plan(id:int,db=Depends(get_db),user=Depends(writer)):
    return clusters.publish_plan(db,id,user)

@router.get('/cluster-claims')
def list_claims(db=Depends(get_db),user=Depends(current_user)):
    return clusters.claims(db)

@router.get('/cluster-runs')
def list_runs(study_id:int|None=None,db=Depends(get_db),user=Depends(current_user)):
    return clusters.list_runs(db,study_id)

@router.post('/cluster-runs')
def create_run(data:ClusterRunInput,db=Depends(get_db),user=Depends(writer)):
    return clusters.create_run(db,data,user)

@router.get('/cluster-runs/{id}')
def get_run(id:int,db=Depends(get_db),user=Depends(current_user)):
    return clusters.get_run(db,id)
