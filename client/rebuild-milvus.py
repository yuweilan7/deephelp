"""Rebuild synthetic corpus into a NEW p00_ collection; never replaces existing data."""
import argparse,json,time
from settings import BASE,milvus_connect
from milvus_schema import schema_and_indexes
p=argparse.ArgumentParser();p.add_argument('--collection',default='p00_rebuilt_'+str(int(time.time())));args=p.parse_args()
assert args.collection.startswith('p00_') and args.collection.replace('_','').isalnum()
c=milvus_connect();assert not c.has_collection(args.collection),'Refusing to overwrite an existing collection'
schema,indexes=schema_and_indexes(c)
(BASE/'client/collection-schema.json').write_text(json.dumps(schema.to_dict(),indent=2,default=str))
rows=[r for r in json.loads((BASE/'client/dataset.json').read_text(encoding='utf-8')) if r['id']!=999]
c.create_collection(args.collection,schema=schema,index_params=indexes,consistency_level='Strong',num_shards=1)
for start in range(0,len(rows),16):c.insert(args.collection,rows[start:start+16])
c.flush(args.collection);c.load_collection(args.collection)
result=c.query(args.collection,filter='id >= 1',output_fields=['id'],limit=100,consistency_level='Strong');assert len(result)==len(rows)==36
search=c.search(args.collection,data=['物流运单查询'],anns_field='sparse',limit=3);assert search[0]
(BASE/'reports/milvus-rebuild.json').write_text(json.dumps({'status':'PASS','collection':args.collection,'rows':len(result),'bm25_search':search},ensure_ascii=False,indent=2),encoding='utf-8')
print('MILVUS_REBUILD PASS',args.collection,len(result),'rows')
c.release_collection(args.collection)
c.close()
