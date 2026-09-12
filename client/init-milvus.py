import json,logging,pathlib
from pymilvus import MilvusClient
from settings import settings,BASE
logging.getLogger('pymilvus').setLevel(logging.CRITICAL)
d=settings()
try:
    root=MilvusClient(uri=d['MILVUS_URI'],user='root',password=d['MILVUS_ROOT_PASSWORD'],timeout=10)
    root.list_databases()
except Exception:
    root=MilvusClient(uri=d['MILVUS_URI'],user='root',password=d['MILVUS_BOOTSTRAP_PASSWORD'],timeout=10)
    root.update_password('root',d['MILVUS_BOOTSTRAP_PASSWORD'],d['MILVUS_ROOT_PASSWORD'])
    root.close()
    root=MilvusClient(uri=d['MILVUS_URI'],user='root',password=d['MILVUS_ROOT_PASSWORD'],timeout=10)
if 'deephelp' not in root.list_databases():root.create_database('deephelp')
if 'deephelp_app' not in root.list_users():root.create_user('deephelp_app',d['MILVUS_PASSWORD'])
if 'deephelp_developer' not in root.list_roles():root.create_role('deephelp_developer')
privileges=['CreateCollection','ShowCollections','DescribeCollection','DropCollection','CreateIndex','IndexDetail','DropIndex','Load','Release','Insert','Delete','Upsert','Search','Query','Flush','GetFlushState','GetLoadState','GetLoadingProgress','GetStatistics']
for privilege in privileges:
    root.grant_privilege_v2('deephelp_developer',privilege,collection_name='*',db_name='deephelp')
root.grant_privilege_v2('deephelp_developer','ListDatabases',collection_name='*',db_name='*')
root.grant_role('deephelp_app','deephelp_developer')
result={'root_password_rotated':True,'database':'deephelp','user':'deephelp_app','role':root.describe_role('deephelp_developer')}
for label,password in [('default_password','Milvus'),('bootstrap_password',d['MILVUS_BOOTSTRAP_PASSWORD'])]:
    rejected=False
    try:
        c=MilvusClient(uri=d['MILVUS_URI'],user='root',password=password,timeout=3);c.list_databases();c.close()
    except Exception:rejected=True
    result[label+'_rejected']=rejected
    assert rejected,label+' still usable'
(BASE/'reports/milvus-rbac.json').write_text(json.dumps(result,indent=2,default=str))
print('MILVUS_AUTH_CONFIGURED: root rotated; default and bootstrap credentials rejected; app scoped to deephelp.')
