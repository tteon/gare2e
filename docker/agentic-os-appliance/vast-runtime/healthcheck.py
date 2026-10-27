import os
from urllib.request import urlopen
urls=['http://127.0.0.1:7474/']
if os.environ.get('GARE2E_DB_ONLY')!='1':
 urls += ['http://127.0.0.1:8000/health','http://127.0.0.1:13133/','http://127.0.0.1:19100/metrics']
for url in urls:
 with urlopen(url,timeout=3) as response:
  if response.status!=200: raise RuntimeError('unhealthy '+url)
# S3/Tempo readback is a separate run acceptance gate; never fake it as liveness.
