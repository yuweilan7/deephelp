"""Synthetic acceptance only. Never emits passwords or DSNs. Run on developer PC."""
import argparse,datetime,json,logging,pathlib,sys,time
from settings import BASE,redis_connect,milvus_connect
logging.getLogger('pymilvus').setLevel(logging.CRITICAL)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('mode',nargs='?',choices=['health','full','post-restart'],default='full')
parser.add_argument('--report-dir',type=pathlib.Path,default=BASE/'reports',help='Directory for the local JSON report')
args=parser.parse_args()
MODE=args.mode
REPORT_DIR=args.report_dir
REPORT_DIR.mkdir(parents=True,exist_ok=True)
out={'mode':MODE,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'viewpoint':'Windows developer PC through SSH tunnel'}
def denied(fn):
    try:fn()
    except Exception:return True
    return False
def redis_test():
    c=redis_connect();r={'ping':c.ping()};assert r['ping']
    if MODE=='health':return r
    if MODE=='post-restart':
        r['cache_lost_as_expected']=c.get('deephelp:p00:restart') is None;assert r['cache_lost_as_expected'];return r
    c.set('deephelp:p00:ttl','中文缓存',ex=2);assert c.get('deephelp:p00:ttl')=='中文缓存';r['ttl']=c.ttl('deephelp:p00:ttl');assert 0<r['ttl']<=2
    time.sleep(2.2);assert c.get('deephelp:p00:ttl') is None
    admin=redis_connect(True);conf=admin.config_get('maxmemory','maxmemory-policy','save','appendonly')
    assert conf['maxmemory']=='67108864' and conf['maxmemory-policy']=='allkeys-lru' and conf['save']=='' and conf['appendonly']=='no'
    r['config']=conf;r['app_config_allowed']=bool(c.config_get('maxmemory'))
    r['full_access']=c.acl_dryrun('deephelp_app','SET','learning:p00:probe','synthetic')=='OK'
    assert r['app_config_allowed'] and r['full_access']
    c.set('deephelp:p00:restart','must disappear',ex=3600)
    r['ttl_expiration']=True;return r
def milvus_test():
    from pymilvus import AnnSearchRequest,WeightedRanker
    from milvus_schema import schema_and_indexes
    c=milvus_connect();r={'databases':c.list_databases(),'server_version':c.get_server_version()}
    if MODE=='health':r['collections']=c.list_collections();c.close();return r
    name='p00_acceptance'
    if MODE=='post-restart':
        c.load_collection(name);rows=c.query(name,filter='id >= 1',output_fields=['id'],limit=100,consistency_level='Strong');assert len(rows)==36
        assert c.query(name,filter='id == 999',output_fields=['id'],consistency_level='Strong')==[]
        r['restart_persistence']=True;r['row_count']=len(rows);c.close();return r
    analyzer={'type':'chinese'}
    tokens=c.run_analyzer(texts=['物流运单查询，包裹延迟怎么办？'],analyzer_params=analyzer);assert tokens;r['chinese_analyzer']=tokens
    schema,indexes=schema_and_indexes(c)
    if c.has_collection(name):c.drop_collection(name)
    t=time.perf_counter();c.create_collection(name,schema=schema,index_params=indexes,consistency_level='Strong',num_shards=1);r['create_seconds']=time.perf_counter()-t
    rows=json.loads((BASE/'client/dataset.json').read_text(encoding='utf-8'))
    for start in range(0,len(rows),16):c.insert(name,rows[start:start+16])
    c.flush(name);c.load_collection(name)
    filt='tenant == "a"';dense=AnnSearchRequest(data=[[1.,0.,0.,0.]],anns_field='dense',param={'metric_type':'COSINE','params':{}},limit=6,expr=filt)
    sparse=AnnSearchRequest(data=['物流运单查询'],anns_field='sparse',param={'metric_type':'BM25','params':{}},limit=6,expr=filt)
    times={}
    for typ,fn in [('dense',lambda:c.search(name,data=[[1.,0.,0.,0.]],anns_field='dense',filter=filt,limit=6,output_fields=['text','tenant'])),('bm25_sparse',lambda:c.search(name,data=['物流运单查询'],anns_field='sparse',filter=filt,limit=6,output_fields=['text','tenant'])),('weighted_hybrid',lambda:c.hybrid_search(name,[dense,sparse],WeightedRanker(0.6,0.4),limit=6,output_fields=['text','tenant']))]:
        t=time.perf_counter();res=fn();times[typ]=(time.perf_counter()-t)*1000;assert res and res[0];assert all(h['entity']['tenant']=='a' for h in res[0]);r[typ]=res
    c.delete(name,ids=[999]);c.flush(name);assert c.query(name,filter='id == 999',output_fields=['id'],consistency_level='Strong')==[]
    r['insert_count']=37;r['delete_verified']=True;r['query_ms']=times;r['metadata_filter']=True
    r['learning_admin_role']='admin' in c.describe_user('deephelp_app')['roles'];assert r['learning_admin_role']
    c.close();return r
try:
    for label,fn in [('redis',redis_test),('milvus',milvus_test)]:
        out[label]=fn();print(label.upper()+': PASS',flush=True)
    out['status']='PASS'
except Exception as e:
    out['status']='FAIL';out['error_type']=type(e).__name__;out['error']=str(e)
    print('ACCEPTANCE_FAILED',type(e).__name__,str(e),flush=True)
finally:
    (REPORT_DIR/('client-'+MODE+'.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
if out['status']!='PASS':sys.exit(1)
