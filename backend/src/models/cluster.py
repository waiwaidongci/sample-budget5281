from sqlalchemy import Column,Integer,String,Text,JSON,Float,Boolean,ForeignKey,UniqueConstraint,Index
from src.config.database import Base

class HouseholdStudy(Base):
    """成簇预注册研究：以家庭为随机化单位的前瞻研究设计。"""
    __tablename__='cluster_studies'
    id=Column(Integer,primary_key=True)
    name=Column(String(120),unique=True,nullable=False)
    source_filename=Column(String(120),nullable=False)
    description=Column(Text,default='')
    p0=Column(Float,nullable=False)
    p1=Column(Float,nullable=False)
    cost_per_contact=Column(Float,nullable=False)
    budget=Column(Float,nullable=False)
    alpha=Column(Float,nullable=False,default=.05)
    power=Column(Float,nullable=False,default=.8)
    simulations=Column(Integer,nullable=False,default=10000)
    icc=Column(Float,nullable=False,default=.15)
    seed=Column(Integer,nullable=False,default=42)
    roster_version=Column(Integer,nullable=False,default=0)
    created_at=Column(String(40),nullable=False)

class AudienceMember(Base):
    """受众成员：一行代表一位可触达对象，家庭与分层由名单给出。"""
    __tablename__='cluster_members'
    __table_args__=(
        UniqueConstraint('study_id','source_key'),
        Index('ix_cluster_members_study_household','study_id','household_key'),
    )
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('cluster_studies.id'),nullable=False)
    source_key=Column(String(100),nullable=False)
    household_key=Column(String(100),nullable=False)
    stratum=Column(String(80),nullable=False)
    created_at=Column(String(40),nullable=False)

class AllocationPlan(Base):
    """分流方案：整户分配到 A/B 组；发布前可重排，发布后不可挪组。"""
    __tablename__='cluster_plans'
    __table_args__=(
        UniqueConstraint('study_id','version'),
        Index('ix_cluster_plans_study_status','study_id','status'),
    )
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('cluster_studies.id'),nullable=False)
    version=Column(Integer,nullable=False)
    status=Column(String(24),nullable=False,default='draft')
    roster_version=Column(Integer,nullable=False)
    seed=Column(Integer,nullable=False)
    parameters=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    allocations=Column(JSON,nullable=False)
    summary=Column(JSON,nullable=False)
    checksum=Column(String(64),nullable=False)
    feasible=Column(Boolean,nullable=False,default=False)
    reason=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
    published_at=Column(String(40),nullable=True)
    published_by=Column(String(120),nullable=True)

class HouseholdClaim(Base):
    """已发布家庭的全局占用登记；同一家庭跨研究互斥。"""
    __tablename__='cluster_claims'
    id=Column(Integer,primary_key=True)
    household_key=Column(String(100),unique=True,nullable=False)
    stratum=Column(String(80),nullable=False)
    group_name=Column(String(1),nullable=False)
    study_id=Column(Integer,ForeignKey('cluster_studies.id'),nullable=False)
    plan_id=Column(Integer,ForeignKey('cluster_plans.id'),nullable=False)
    created_at=Column(String(40),nullable=False)

class ClusterRun(Base):
    """已发布方案的排队研究：冻结同版名单、分配与户内相关参数。"""
    __tablename__='cluster_runs'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('cluster_studies.id'),nullable=False)
    plan_id=Column(Integer,ForeignKey('cluster_plans.id'),nullable=False)
    name=Column(String(120),nullable=False)
    status=Column(String(20),default='queued')
    parameters=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    checksum=Column(String(64),nullable=False)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
