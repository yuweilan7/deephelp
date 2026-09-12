"""Synthetic acceptance only. Never emits passwords or DSNs. Run on developer PC."""
import datetime,json,logging,pathlib,socket,sys,time,uuid
from settings import BASE,settings,mysql_connect,redis_connect,milvus_connect
logging.getLogger('pymilvus').setLevel(logging.CRITICAL)
MODE=sys.argv[1] if len(sys.argv)>1 else 'full'
out={'mode':MODE,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'viewpoint':'Windows developer PC through SSH tunnel'}
def denied(fn):
    try:fn()
    except Exception:return True
    return False
def mysql_test():
    c=mysql_connect();q=c.cursor();r={}
    q.execute('SELECT VERSION(),DATABASE(),1,@@character_set_database,@@session.time_zone,@@global.max_connections,@@innodb_buffer_pool_size')
    r['server']=list(q.fetchone());assert r['server'][1:] == ['deephelp',1,'utf8mb4','+00:00',20,134217728]
    if MODE=='health':c.close();return r
    q.execute('CREATE TABLE IF NOT EXISTS p00_probe (id INT PRIMARY KEY, token VARCHAR(100) NOT NULL UNIQUE, content VARCHAR(200) NOT NULL, created_at DATETIME(6) NOT NULL, category VARCHAR(50), INDEX idx_category(category)) ENGINE=InnoDB')
    if MODE=='post-restart':
        q.execute('SELECT content FROM p00_probe WHERE id=1');assert q.fetchone()[0]=='中文事务已提交😀';r['restart_persistence']=True;c.close();return r
    q.execute('DELETE FROM p00_probe');c.commit()
    q.execute('INSERT INTO p00_probe VALUES (1,%s,%s,UTC_TIMESTAMP(6),%s)',('committed','中文事务已提交😀','p00'));c.commit()
    q.execute('INSERT INTO p00_probe VALUES (2,%s,%s,UTC_TIMESTAMP(6),%s)',('rolled-back','应回滚','p00'));c.rollback()
    q.execute('SELECT COUNT(*) FROM p00_probe WHERE id=2');assert q.fetchone()[0]==0
    assert denied(lambda:q.execute('INSERT INTO p00_probe VALUES(3,%s,%s,UTC_TIMESTAMP(6),%s)',('committed','duplicate','p00')));c.rollback()
    q.execute('SELECT content,created_at,UTC_TIMESTAMP(6) FROM p00_probe WHERE id=1');row=q.fetchone();assert row[0]=='中文事务已提交😀' and abs((row[1]-row[2]).total_seconds())<120
    q.execute('SHOW INDEX FROM p00_probe');assert 'idx_category' in [x[2] for x in q.fetchall()]
    r['isolation_mysql_system_denied']=denied(lambda:q.execute('SELECT * FROM mysql.user'))
    r['isolation_create_database_denied']=denied(lambda:q.execute('CREATE DATABASE p00_forbidden'))
    assert r['isolation_mysql_system_denied'] and r['isolation_create_database_denied']
    q.execute('SHOW GRANTS');r['grants']=[x[0] for x in q.fetchall()]
    c.close()
    opened=[]
    try:
        for _ in range(16):opened.append(mysql_connect())
        r['connection_17_denied']=denied(lambda:opened.append(mysql_connect()))
        assert r['connection_17_denied']
    finally:
        for conn in opened:conn.close()
    r.update(utf8mb4=True,commit=True,rollback=True,unique_key=True,index=True,utc_datetime=True)
    return r
def redis_test():
    c=redis_connect();r={'ping':c.ping()};assert r['ping']
    if MODE=='health':return r
    if MODE=='post-restart':
        r['cache_lost_as_expected']=c.get('deephelp:p00:restart') is None;assert r['cache_lost_as_expected'];return r
    c.set('deephelp:p00:ttl','中文缓存',ex=2);assert c.get('deephelp:p00:ttl')=='中文缓存';r['ttl']=c.ttl('deephelp:p00:ttl');assert 0<r['ttl']<=2
    time.sleep(2.2);assert c.get('deephelp:p00:ttl') is None
    admin=redis_connect(True);conf=admin.config_get('maxmemory','maxmemory-policy','save','appendonly')
    assert conf['maxmemory']=='67108864' and conf['maxmemory-policy']=='allkeys-lru' and conf['save']=='' and conf['appendonly']=='no'
    r['config']=conf;r['app_config_denied']=denied(lambda:c.config_get('*'));r['other_prefix_denied']=denied(lambda:c.set('other:probe','denied'))
    assert r['app_config_denied'] and r['other_prefix_denied']
    c.set('deephelp:p00:restart','must disappear',ex=3600)
    r['ttl_expiration']=True;return r
def milvus_test():
    from pymilvus import MilvusClient,DataType,Function,FunctionType,AnnSearchRequest,WeightedRanker
    c=milvus_connect();r={'databases':c.list_databases(),'server_version':c.get_server_version()}
    if MODE=='health':r['collections']=c.list_collections();c.close();return r
    name='p00_acceptance'
    if MODE=='post-restart':
        c.load_collection(name);rows=c.query(name,filter='id >= 1',output_fields=['id'],limit=100,consistency_level='Strong');assert len(rows)==36
        assert c.query(name,filter='id == 999',output_fields=['id'],consistency_level='Strong')==[]
        r['restart_persistence']=True;r['row_count']=len(rows);c.close();return r
    analyzer={'type':'chinese'}
    tokens=c.run_analyzer(texts=['物流运单查询，包裹延迟怎么办？'],analyzer_params=analyzer);assert tokens;r['chinese_analyzer']=tokens
    schema=MilvusClient.create_schema(auto_id=False,enable_dynamic_field=False)
    schema.add_field('id',DataType.INT64,is_primary=True)
    schema.add_field('text',DataType.VARCHAR,max_length=1024,enable_analyzer=True,analyzer_params=analyzer)
    schema.add_field('tenant',DataType.VARCHAR,max_length=20)
    schema.add_field('dense',DataType.FLOAT_VECTOR,dim=4)
    schema.add_field('sparse',DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(Function(name='text_bm25',function_type=FunctionType.BM25,input_field_names=['text'],output_field_names=['sparse']))
    indexes=c.prepare_index_params();indexes.add_index(field_name='dense',index_type='FLAT',metric_type='COSINE');indexes.add_index(field_name='sparse',index_type='SPARSE_INVERTED_INDEX',metric_type='BM25')
    if c.has_collection(name):c.drop_collection(name)
    t=time.perf_counter();c.create_collection(name,schema=schema,index_params=indexes,consistency_level='Strong',num_shards=1);r['create_seconds']=time.perf_counter()-t
    samples=[('物流运单查询 包裹延迟 联系客服',[1.,0.,0.,0.]),('账户密码重置 登录帮助',[0.,1.,0.,0.]),('商品退货 退款申请 售后服务',[0.,0.,1.,0.])]
    rows=[{'id':i,'text':samples[(i-1)%3][0],'tenant':'a' if i<=24 else 'b','dense':samples[(i-1)%3][1]} for i in range(1,37)]
    rows.append({'id':999,'text':'待删除 测试记录','tenant':'a','dense':[0.,0.,0.,1.]})
    (BASE/'client/dataset.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    for start in range(0,len(rows),16):c.insert(name,rows[start:start+16])
    c.flush(name);c.load_collection(name)
    filt='tenant == "a"';dense=AnnSearchRequest(data=[[1.,0.,0.,0.]],anns_field='dense',param={'metric_type':'COSINE','params':{}},limit=6,expr=filt)
    sparse=AnnSearchRequest(data=['物流运单查询'],anns_field='sparse',param={'metric_type':'BM25','params':{}},limit=6,expr=filt)
    times={}
    for typ,fn in [('dense',lambda:c.search(name,data=[[1.,0.,0.,0.]],anns_field='dense',filter=filt,limit=6,output_fields=['text','tenant'])),('bm25_sparse',lambda:c.search(name,data=['物流运单查询'],anns_field='sparse',filter=filt,limit=6,output_fields=['text','tenant'])),('weighted_hybrid',lambda:c.hybrid_search(name,[dense,sparse],WeightedRanker(0.6,0.4),limit=6,output_fields=['text','tenant']))]:
        t=time.perf_counter();res=fn();times[typ]=(time.perf_counter()-t)*1000;assert res and res[0];assert all(h['entity']['tenant']=='a' for h in res[0]);r[typ]=res
    c.delete(name,ids=[999]);c.flush(name);assert c.query(name,filter='id == 999',output_fields=['id'],consistency_level='Strong')==[]
    r['insert_count']=37;r['delete_verified']=True;r['query_ms']=times;r['metadata_filter']=True
    r['create_database_denied']=denied(lambda:c.create_database('p00_forbidden'));assert r['create_database_denied']
    c.close();return r
try:
    for label,fn in [('mysql',mysql_test),('redis',redis_test),('milvus',milvus_test)]:
        out[label]=fn();print(label.upper()+': PASS',flush=True)
    out['status']='PASS'
except Exception as e:
    out['status']='FAIL';out['error_type']=type(e).__name__;out['error']=str(e)
    print('ACCEPTANCE_FAILED',type(e).__name__,str(e),flush=True)
finally:
    (BASE/'reports'/('client-'+MODE+'.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
if out['status']!='PASS':sys.exit(1)
