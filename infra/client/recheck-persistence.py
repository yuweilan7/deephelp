"""Non-mutating search and persistence checks for the 12-minute observation."""
import json,time
from pymilvus import AnnSearchRequest,WeightedRanker
from settings import BASE,milvus_connect,mysql_connect,redis_connect
c=milvus_connect();name='p00_acceptance';r={}
c.load_collection(name)
reqs=[AnnSearchRequest([[1.,0.,0.,0.]],'dense',{'metric_type':'COSINE'},6,expr='tenant == "a"'),AnnSearchRequest(['物流运单查询'],'sparse',{'metric_type':'BM25'},6,expr='tenant == "a"')]
t=time.perf_counter();res=c.hybrid_search(name,reqs,WeightedRanker(0.6,0.4),limit=6,output_fields=['tenant','text']);r['hybrid_ms']=(time.perf_counter()-t)*1000
assert res[0] and all(h['entity']['tenant']=='a' for h in res[0]);r['results']=res
assert len(c.query(name,filter='id>=1',output_fields=['id'],limit=100,consistency_level='Strong'))==36
assert c.query(name,filter='id==999',output_fields=['id'],consistency_level='Strong')==[]
with mysql_connect() as db:
 with db.cursor() as q:
  q.execute('SELECT content FROM p00_probe WHERE id=1');assert q.fetchone()[0]=='中文事务已提交😀'
assert redis_connect().ping()
c.close();r['status']='PASS';r['time']=time.time()
with (BASE/'reports/observation-queries.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(r,ensure_ascii=False)+'\n')
print('PERSISTED_HYBRID_FILTER_AND_SQL PASS',round(r['hybrid_ms'],2),'ms')
