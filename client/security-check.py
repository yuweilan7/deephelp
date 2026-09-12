import json,logging
import pymysql,redis
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
r['mysql_wrong_password_rejected']=rejected(lambda:pymysql.connect(host='127.0.0.1',port=int(d['MYSQL_PORT']),user='deephelp_app',password='invalid-p00-probe',connect_timeout=5))
r['redis_unauthenticated_rejected']=rejected(lambda:redis.Redis(host='127.0.0.1',port=int(d['REDIS_PORT']),socket_timeout=5).ping())
r['milvus_unauthenticated_rejected']=rejected(lambda:MilvusClient(uri=d['MILVUS_URI'],timeout=5).list_databases())
c=milvus_connect();r['milvus_app_list_users_rejected']=rejected(lambda:c.list_users());c.close()
assert all(r.values()),r
(BASE/'reports/auth-negative-tests.json').write_text(json.dumps(r,indent=2))
print('AUTH_NEGATIVE_TESTS PASS')
