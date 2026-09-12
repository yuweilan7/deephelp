import os,pathlib
BASE=pathlib.Path(__file__).resolve().parents[1]
def read_env(p):
    return dict(l.split('=',1) for l in p.read_text(encoding='utf-8-sig').splitlines() if l and not l.startswith('#') and '=' in l)
def settings(admin=False):
    p=BASE/'config/connections.env'
    if not p.exists():p=BASE/'config/connections.env.example'
    d=read_env(p)
    secret=pathlib.Path(os.getenv('DEEPHELP_SECRET_FILE',str(pathlib.Path.home()/'.deephelp/.env.secret')))
    d.update(read_env(secret))
    return d
def mysql_connect():
    import pymysql
    d=settings()
    return pymysql.connect(host=d['MYSQL_HOST'],port=int(d['MYSQL_PORT']),user=d['MYSQL_USER'],password=d['MYSQL_PASSWORD'],database=d['MYSQL_DATABASE'],charset='utf8mb4',connect_timeout=10,read_timeout=30,write_timeout=30)
def redis_connect(admin=False):
    import redis
    d=settings()
    return redis.Redis(host=d['REDIS_HOST'],port=int(d['REDIS_PORT']),username='deephelp_admin' if admin else d['REDIS_USER'],password=d['REDIS_ADMIN_PASSWORD' if admin else 'REDIS_PASSWORD'],decode_responses=True,socket_timeout=10)
def milvus_connect(admin=False,db=None):
    from pymilvus import MilvusClient
    d=settings()
    return MilvusClient(uri=d['MILVUS_URI'],user='root' if admin else d['MILVUS_USER'],password=d['MILVUS_ROOT_PASSWORD' if admin else 'MILVUS_PASSWORD'],db_name=db or ('default' if admin else d['MILVUS_DATABASE']),timeout=30)
