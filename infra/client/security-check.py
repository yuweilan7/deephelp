import json,logging
import redis
from pymilvus import MilvusClient
from settings import BASE,settings,milvus_connect
logging.disable(logging.CRITICAL)
d=settings();r={}
def rejected(fn):
    try:
        conn=fn()
        if hasattr(conn,'close'):conn.close()
        return False
    except Exception:return True
r['redis_unauthenticated_rejected']=rejected(lambda:redis.Redis(host='127.0.0.1',port=int(d['REDIS_PORT']),socket_timeout=5).ping())
r['milvus_unauthenticated_rejected']=rejected(lambda:MilvusClient(uri=d['MILVUS_URI'],timeout=5).list_databases())
c=milvus_connect();r['milvus_app_admin_access']=('deephelp_app' in c.list_users());c.close()
assert all(r.values()),r
(BASE/'reports/auth-negative-tests.json').write_text(json.dumps(r,indent=2))
print('AUTH_NEGATIVE_TESTS PASS')
