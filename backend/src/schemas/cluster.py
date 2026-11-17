from pydantic import BaseModel,Field

class HouseholdStudyInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    source_filename:str=Field(min_length=3,max_length=120)
    description:str=Field(default='',max_length=2000)
    p0:float
    p1:float
    cost_per_contact:float
    budget:float
    alpha:float=.05
    power:float=.8
    simulations:int=10000
    icc:float=.15
    seed:int=42

class AudienceMemberInput(BaseModel):
    source_key:str=Field(min_length=1,max_length=100)
    household_key:str=Field(min_length=1,max_length=100)
    stratum:str=Field(min_length=1,max_length=80)

class AudienceImportInput(BaseModel):
    members:list[AudienceMemberInput]

class PlanGenerateInput(BaseModel):
    alpha:float|None=None
    power:float|None=None
    simulations:int|None=None
    icc:float|None=None
    seed:int|None=None

class ClusterRunInput(BaseModel):
    plan_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=120)
