import os,time,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.run import Run
from src.models.cluster import ClusterRun
from src.models import user,study,observation,review,audit,cluster
from src.services.algorithm import calculate
from src.services.cluster_algorithm import analyze as cluster_analyze
from src.utils.serializers import checksum
redis=Redis.from_url(os.environ['REDIS_URL'])
ARTIFACTS=Path(os.environ.get('ARTIFACT_DIR','/app/artifacts'))
ARTIFACTS.mkdir(parents=True,exist_ok=True)

def requeue_pending():
    # Recover jobs interrupted after dequeue. A project runs exactly one worker.
    with Session() as db:
        for run in db.scalars(select(Run).where(Run.status.in_(['queued','running']))):
            redis.rpush('analysis-queue',run.id)
        for run in db.scalars(select(ClusterRun).where(ClusterRun.status.in_(['queued','running']))):
            redis.rpush('cluster-queue',run.id)

def store_result(kind,run,result,path):
    if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
    result['input_sha256']=run.checksum
    if kind=='cluster':
        result['plan_id']=run.plan_id;result['plan_version']=run.snapshot['plan_version']
        result['roster_version']=run.snapshot['roster_version']
        result['roster_checksum']=run.snapshot['roster_checksum']
    run.result=result;run.status='completed'
    Path(path).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))

def handle(kind,raw_id):
    id=int(raw_id)
    with Session() as db:
        run=db.get(ClusterRun if kind=='cluster' else Run,id)
        if not run or run.status in ('completed','failed'): return
        run.status='running';db.commit()
        try:
            if kind=='cluster':
                result=cluster_analyze(run.snapshot['allocations'],run.parameters)
                store_result(kind,run,result,ARTIFACTS/f'cluster-run-{id}.json')
            else:
                result=calculate(run.snapshot,run.parameters)
                store_result(kind,run,result,ARTIFACTS/f'run-{id}.json')
        except Exception as e:
            logging.exception('%s analysis failed',kind);run.status='failed';run.error=str(e)
        db.commit()

requeue_pending()
# 两条队列交替轮询，避免成簇研究被连续的独立研究任务饿死。
while True:
    Path('/tmp/worker-heartbeat').touch()
    did=False
    for queue,kind in (('analysis-queue','legacy'),('cluster-queue','cluster')):
        job=redis.blpop(queue,timeout=1)
        if job:
            did=True
            handle(kind,job[1])
    if not did: time.sleep(.2)
